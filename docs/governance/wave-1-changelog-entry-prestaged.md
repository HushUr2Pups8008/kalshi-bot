# Wave-1 deploy — pre-staged CHANGELOG entry

**Status:** pre-stage; copy into `CHANGELOG.md` at the Day-13 / Day-14 close commit. **Do not insert into `CHANGELOG.md` pre-soak-close** — that would lock in the version number before the actual deploy.
**Drafted:** 2026-05-04 (during PROFIT-PHASE2-001 soak)
**Rationale:** reduces deploy-day cognitive load. Operator copies the relevant block, fills in the actual VERSION value, runs the pre-commit hook to sync README badges, commits.

## Version-bump decision

Wave-1 lands six behavioural changes:

1. PROFIT-OBS-005 cooldown sentinel-default fix (observability behaviour)
2. PROFIT-MATCH-001 (B′) token-guard refinement (match-surface tightening)
3. PROFIT-OBS-003 BlendTask SKIPPED-emission (new SKIPPED records emitted)
4. PROFIT-EXEC-002 series-correlation guard (correlated-burst suppression)
5. PROFIT-GOV-003 governance_monitor.py fix (governance-side fix)
6. PROFIT-EDGE-004 Lever A.1 source-class classifier prerequisite (classification only; no archive lift)

Multi-feature with behavioural changes ⇒ **minor bump**: `0.29.59 → 0.30.0`.

If the operator deploys Wave-1 in stages (one per day per the rehearsal checklist §1-§4 cadence), bump to `0.29.60` per stage and reserve `0.30.0` for the day-13 base-stack-closed commit. Either choice is defensible; the pre-staged block below is for the all-in-one bump.

## Pre-staged CHANGELOG block

Insert ABOVE the current `## [0.29.59] - 2026-05-02` heading in `CHANGELOG.md`:

> **Audit note:** the `[text](docs/...)` link forms inside the fenced block below
> are written with **repo-root-relative** paths because they're meant to resolve
> from `CHANGELOG.md` (which lives at repo root). The links resolve correctly
> once the block is pasted into `CHANGELOG.md`. `scripts/doc_xref_audit.py`
> honors the `<!-- audit-skip-block -->` markers wrapping the fenced block.

<!-- audit-skip-block: prestaged CHANGELOG content; links are repo-root-relative for paste target -->

```markdown
## [0.30.0] - 2026-05-08  (or actual deploy date if §8.5.1 early-close path is taken)

### Added (PROFIT-PHASE2-001 — Wave-1 base stack post-soak deploy)

Six behavioural changes shipped together at the close of the
PROFIT-PHASE2-001 governance shadow-soak (2026-05-01 → 2026-05-08
under §8.5.1 early-close, per `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md`).
Pre-deploy validation per the rehearsal checklist
[`docs/governance/post-soak-close-rehearsal-checklist.md`](docs/governance/post-soak-close-rehearsal-checklist.md).

#### PROFIT-OBS-005 — cooldown sentinel-default fix

Bug: `Executor._cooldown_remaining()` returned `0.0` for keys not in
`self._cooldowns`, indistinguishable from "cooldown just expired."
Downstream observability could not differentiate first-time-trade
from recently-cooled-down. Fixed: returns `None` for missing keys;
callers updated to handle `None` semantics. Spec:
[`docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md`](docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md).

#### PROFIT-MATCH-001 (B′) — token-guard refinement

Match-surface tightening: B-suppression now uses substring
containment over `ticker_lower` rather than `_tokenize` set-difference
(per spec §5.1 addendum). Pre-deploy validation:
- `scripts/simulations/match001_tokenization_equivalence_audit.py`
  (Codex `e5b7213`) — pinned divergence
- `tests/test_match001_tokenization_equivalence_regression.py`
  (`d61da2d`) — 4 archive-size-invariant assertions
- `scripts/simulations/match001_bprime_false_negative_audit.py`
  (Codex `8001a16`) — 0 likely false negatives
- `scripts/simulations/match001_bprime_false_suppression_audit.py`
  (`83a9477`) + Codex spec-parity (`b56c261`) — orthogonal to
  existing pre-fix suppression; clean deploy

#### PROFIT-OBS-003 — BlendTask SKIPPED-emission

`BlendTask` now emits `SKIPPED` records carrying the gate-killing
`reason` (G1-G6) and `signal_meta`. Enables Lever B G1 attribution
and the post-OBS-003 SKIPPED-stream attribution audit
(`scripts/simulations/post_obs003_skipped_attribution_audit.py`,
`8bd7157`).

#### PROFIT-EXEC-002 — series-correlation guard

Suppresses correlated-burst trades within a single market series
within a configurable window. Pre-deploy validation: archive replay
showed 2 FISA-class paper-trade bursts suppressed. Spec:
[`docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md`](docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md). NOTE: this is the SAME-series guard (Wave 1 / EXEC-002), not the CROSS-series guard (Wave 3 / Lever C — separate spec).

#### PROFIT-GOV-003 — governance_monitor.py fix

Governance-side fix per
[`docs/superpowers/specs/2026-05-03-governance-monitor-fix-design.md`](docs/superpowers/specs/2026-05-03-governance-monitor-fix-design.md).
Soak-shadow-validated with 0 KILL_SWITCH / VALIDATION_ERROR /
batch_aborted across the full 14-day window.

#### PROFIT-EDGE-004 Lever A.1 — source-class classifier prerequisite

Classifier-only change (no archive trade-rate lift expected; Codex
`8001a16` archive replay confirmed). Adds tokens to
`main.py:_source_class_for_evidence` so existing official /
specialist sources classify correctly. Prerequisite to A.1+ feed
onboarding (Wave 2). Spec:
[`docs/superpowers/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md`](docs/superpowers/specs/2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md).

### Soak validation

PROFIT-PHASE2-001 governance shadow-soak (2026-05-01 → 2026-05-08 under §8.5.1)
recorded:
- ≥ ~80 fast cycles + ≥ ~12 deep cycles, cadence honoured
  throughout
- 0 KILL_SWITCH events
- 0 `batch_aborted=True` events
- 0 VALIDATION_ERROR events
- 7 PARSE_ERROR events on day-1/-2 (all background; trailing-window
  0% by day-3+)
- distinct-targets growth steady through day-7+

Pre-deploy mid-soak reports:
- [`docs/governance/2026-05-04-day-4-mid-soak-confirmation.md`](docs/governance/2026-05-04-day-4-mid-soak-confirmation.md)
- `docs/governance/2026-05-07-day-7-pending-mid-soak-confirmation.md` (created at fire-time on 2026-05-07/08; pre-Wave-1-deploy this file does not yet exist)
- snapshot-1 through snapshot-5 (day-1 through day-3)

### Removed `pytest.mark.xfail` markers (deploy commit)

The Wave-1 close commit removes the strict-xfail markers from
the following pre-loaded harnesses (each test xpasses on the deploy
commit; the marker MUST be removed in the same hunk to keep CI
green):

- `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1` (6 markers)
- `tests/test_executor.py::TestObs005CooldownSentinelDefault` (multiple)
- `tests/test_market_matcher.py::TestMatch001TokenGuardRefinement` (multiple)
- `tests/test_blend_task.py::TestObs003SkippedEmission` (multiple)
- `tests/test_executor.py::TestExec002SeriesCorrelationGuard` (multiple)
- `tests/test_governance_monitor.py::TestGov003Fix` (multiple)

The Wave-2 (A.1+) and A.1+1.5 harnesses remain xfail-strict —
those deploy in Wave 2 (≥ Day 14), not Wave 1.

### Operator deploy commands (pre-staged)

Per the rehearsal checklist [§5](docs/governance/post-soak-close-rehearsal-checklist.md#5-day-13--wave-1-base-stack-closed-governance_monitor-fix-lands-alongside):

```bash
# Bump VERSION
echo "0.30.0" > VERSION
git add VERSION  # pre-commit hook syncs README

# Land the actual code changes (one logical commit per spec, OR a
# single bundled commit per operator preference; the per-spec
# approach is safer for rollback)

# Confirm full pytest sweep
.venv/bin/python -m pytest -q

# Confirm ruff clean
.venv/bin/ruff check .

# Tag
git tag -a v0.30.0 -m "Wave-1 base-stack post-soak deploy"
git push origin main --tags
```

```

<!-- /audit-skip-block -->

## Cross-links

- `docs/governance/post-soak-close-rehearsal-checklist.md` §5 — deploy commands
- `docs/governance/post-soak-rollback-runbook.md` — incident-response runbook
- `docs/governance/edge-004-closure-path-tldr.md` v2 — current EDGE-004 state
- `docs/governance/2026-05-03-edge004-wave1-plus-wave2-unified-trade-rate-forecast.md` — pre-deploy expected state
