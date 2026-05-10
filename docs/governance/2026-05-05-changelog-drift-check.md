# 2026-05-05 — CHANGELOG.md drift check vs pre-staged Wave-1/2/3 entries

**Type:** read-only review (Claude task per Implementation Contract §9 — review).
**Source:** `CHANGELOG.md` (HEAD `753ec36`) vs `docs/_archive/governance/wave-1-changelog-entry-prestaged.md` (ARCHIVED Stream G R49) + `wave-2-wave-3-changelog-entries-prestaged.md`.
**Audience:** operator before Wave-1 commit 6 deploy (when 0.30.0 lands and the Wave-1 CHANGELOG block is inserted).
**No edits applied.** Findings only.

## TL;DR

`CHANGELOG.md` HEAD = **0.29.59 - 2026-05-02**. Wave-1 / Wave-2 / Wave-3 are NOT yet inserted (correct — pre-staged blocks ARE pre-deploy artifacts). **2 MEDIUM drift findings** in the Wave-2 pre-staged block due to recent (cycle 2) lock decisions; **1 LOW** drift in the version-sequence table.

Recommend updating `wave-2-wave-3-changelog-entries-prestaged.md` with the cycle-2 deltas before Wave-2 deploy, so the operator's deploy-day paste is correct.

## Drift findings

### F1 (MEDIUM) — Version sequence outdated

**Source:** `wave-2-wave-3-changelog-entries-prestaged.md` lines 8-13 — version sequence table.

```
| 1 | OBS-005 / MATCH-001 / OBS-003 / EXEC-002 / GOV-003 / Lever A.1 | 2026-05-08 (early-close path) or 2026-05-15 (default) | 0.30.0 |
| 2 | Lever A.1+ first feed (option-A or option-C-Branch-C-fallback) | 2026-05-15+ (early-close) or 2026-05-22+ (default) | 0.30.1 |
| 3 | Lever B G1=0.04 + Lever C cross-series guard | 2026-06-06+ (early-close) or 2026-06-13+ (default) | 0.31.0 |
```

**Reality at HEAD:**

- Wave-1 dates were correct (2026-05-08 early-close path holds).
- Wave-2 first-feed: per `2026-05-05-wave-2-deploy-day-timing.md`, Branch A starts 2026-05-18 (passive observation, NO version bump); Branch C deploys 2026-06-02 IF Branch A returns 0 PAPER_TRADE. Wave-2's ACTUAL trigger date is 2026-06-02, not 2026-05-15.
- Wave-3 dates: per `2026-05-05-wave-3-deploy-day-timing.md`, Wave-3 commit 1 (Lever B) fires 2026-06-17; commit 2 (Lever C) fires 2026-07-01. Inter-commit cadence is 14 d, not co-deployed. Pre-staged block table conflates Lever B + Lever C as a single Wave-3 entry.

**Recommended fix to `wave-2-wave-3-changelog-entries-prestaged.md`:**

```
| 1 | Wave-1 base stack | 2026-05-08+ | 0.30.0 |
| 2 | Wave-2 Branch A start (passive observe; NO code change) | 2026-05-18 | n/a (no bump) |
| 3 | Wave-2 Branch C deploy (legal-analyst onboard; only if Branch A stalls) | 2026-06-02+ | 0.31.0 |
| 4 | Wave-2 option-A deploy (geopolitics specialist; parallel-discretion or fallback) | 2026-05-18+ (parallel) or 2026-06-16+ (fallback) | 0.31.x or 0.32.0 |
| 5 | Wave-3 commit 1 (Lever B G1=0.04) | 2026-06-17+ | 0.32.0 (or 0.33.0 if option-A landed) |
| 6 | Wave-3 commit 2 (Lever C cross-series) | 2026-07-01+ | 0.33.0 (or 0.34.0) |
```

**Severity MEDIUM** because operator deploy-day paste-from-table operation will use stale dates without this fix. **Remediable in 5 min.**

### F2 (MEDIUM) — Wave-3 pre-staged block missing 2026-05-05 lock decisions

**Source:** `wave-2-wave-3-changelog-entries-prestaged.md` Wave-3 block (`## [0.31.0]`) — current text references "Lever B G1=0.04" + "Lever C cross-series guard" generically; doesn't mention the 2026-05-05 LOCK addenda.

**Reality at HEAD:**

- Lever B 0.04 floor LOCKED 2026-05-05 per `docs/_archive/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` (ARCHIVED Stream G R34; pins primary 0.04 / failsafe 0.08 / 2× ratio invariant; lifts harness pre-load deferral).
- Lever C v1 LOCKED 2026-05-05 per `docs/_archive/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` (ARCHIVED Stream G R33; pins §3.2 normalized hash / 3600 s default / record-after-gate-pass placement / INV-6 boundary attestation).

The pre-staged block should reference these addenda by name so the deploy-day operator's CHANGELOG entry includes the correct authority chain.

**Recommended fix:** Wave-3 pre-staged block should add:

```markdown
NOTE: Lever B floor (0.04 / failsafe 0.08 / 2× ratio invariant) locked
2026-05-05 per [`docs/_archive/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md`](...) (ARCHIVED Stream G R34).

NOTE: Lever C v1 implementation (§3.2 normalized hash / 3600 s default
window / record-after-gate-pass placement / INV-6 boundary attestation)
locked 2026-05-05 per [`docs/_archive/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md`](...) (ARCHIVED Stream G R33).
```

**Severity MEDIUM** because if a future post-deploy spec audit traces back to the deployed code, the CHANGELOG entry should point at the LOCKED authority for the implementation choices. **Remediable in 5 min.**

### F3 (LOW) — Wave-2 pre-staged block uses obsolete "option-C-Branch-C-fallback" terminology

**Source:** `wave-2-wave-3-changelog-entries-prestaged.md` Wave-2 block — references "option-C-Branch-C-fallback" and "Option-C-Branch-C" in a few places.

**Reality at HEAD:**

Per the cycle-2 nomenclature cleanup (`2026-05-05-lever-d-nomenclature-cleanup-audit.md` + Wave-2 branch decision-table edits this cycle), the canonical names are:

- **Branch A** = passive Google News observe
- **Branch C** = open-RSS legal-analyst onboard
- **option-A** = geopolitics specialist (per A.1+ parent spec terminology)

The pre-staged block's "option-C-Branch-C-fallback" should simplify to "Branch C." Same correctness; cleaner.

**Severity LOW** because operator deploy-day paste will be intelligible; cosmetic only.

## Confirmed clean

- `CHANGELOG.md` HEAD = 0.29.59 - 2026-05-02; matches `VERSION` file content (0.29.59) ✅
- Wave-1 pre-staged block (`docs/_archive/governance/wave-1-changelog-entry-prestaged.md` ARCHIVED Stream G R49) matches the 6-commit lineup that landed in Wave-1 dry-run + spec-locked order ✅
- Removed-xfail-markers list in Wave-1 pre-staged block matches existing test files at `wave-1-deploy-commit-order-decision.md` ✅
- README badges currently show v0.29.59 (per pre-commit hook check; not validated in this audit pass) — assume clean
- `evidence_store_schema.md`, `IMPLEMENTATION_CONTRACT.md`, `profit_path_debt_log.md`, `ROADMAP.md` — not in CHANGELOG scope; clean

## Recommended pre-deploy actions

1. **Apply F1** (version sequence table refresh) before Wave-2 first deploy (2026-05-18 Branch A start, or 2026-06-02 Branch C deploy). Trivial 5-min edit.
2. **Apply F2** (Wave-3 LOCK addenda references) when Wave-2 stalls AND Wave-3 deploy decision is made (~2026-06-16). Trivial 5-min edit.
3. **Apply F3** (cosmetic nomenclature) anytime; no urgency.

**No CHANGELOG.md edits applied at this audit time.** Pre-staged block edits are operator-side deploy-prep work; this audit identifies them so the operator's deploy-day paste is correct.

## Out of scope

- README content drift check — separate audit.
- Pre-commit hook health audit — Codex task this cycle.
- Release-tag inventory — Codex task this cycle.

## Cross-links

- `CHANGELOG.md` — current HEAD (0.29.59)
- `docs/_archive/governance/wave-1-changelog-entry-prestaged.md` — Wave-1 block (no drift) (ARCHIVED Stream G R49)
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` — Wave-2/3 blocks (drift findings F1-F3 land here)
- `docs/governance/2026-05-05-wave-2-deploy-day-timing.md` — Wave-2 timing (this cycle)
- `docs/governance/2026-05-05-wave-3-deploy-day-timing.md` — Wave-3 timing (this cycle)
- `docs/governance/wave-2-deploy-commit-order-decision.md` — Wave-2 commit order (this cycle)
