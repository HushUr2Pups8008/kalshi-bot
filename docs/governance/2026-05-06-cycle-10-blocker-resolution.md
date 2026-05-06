# Cycle-10 blocker resolution

**Drafted:** 2026-05-06.
**Source:** Codex's cycle-10 status report listed 3 blockers that required either operator action or contract clarity. This doc records resolutions so future cycles do not re-attempt the same items as if they were oversight.

## Blocker 1 — Plist fixtures: UNBLOCKED

**Codex status:** "Canonical installed plist fixture files were not committed. The safety layer blocked printing full plist contents; I added `tests/fixtures/installed_plists/README.md` documenting the capture rule instead of synthesizing fixtures."

**Resolution:** Claude's sandbox permits direct read of `~/Library/LaunchAgents/`. Cycle-10.5 captured all 6 plists byte-faithfully via the audit script's `normalize()` function (token-level substitutions only per IC §15 Rule 2):

```bash
.venv/bin/python -c "..."  # see fixtures README §"Refresh procedure"
```

Resulting fixtures: 6 files in `tests/fixtures/installed_plists/`. Both audit modes now PASS:

```
.venv/bin/python scripts/launchd_template_equivalence_audit.py --installed   # PASS
.venv/bin/python scripts/launchd_template_equivalence_audit.py --fixtures    # PASS
```

`tests/test_launchd_plist_template_render.py` previously skipped the `--fixtures` test (`pytest.skip(f"canonical installed-plist fixtures absent")`). Now: 3 pass / 0 skip.

**Operator follow-up:** none. Fixtures will need refresh ONLY if templates change AND the operator has applied the change to installed plists. Procedure documented in `tests/fixtures/installed_plists/README.md`.

## Blocker 2 — Wave-1 6-commit deploy hunks: INTENTIONAL DEFERRAL (not blocked)

**Codex status:** "I did not fabricate Wave-1 six patch hunks ... Those need exact deploy-code decisions or an approved source patch, not inference."

**Resolution:** Authoring `.patch` files for the 6 Wave-1 commits is **out of scope** for cycle-10 by design. The cycle-10 artifact `docs/governance/wave-1-commit-messages-prestaged.md` is the operator's deploy-day guidance for Wave-1; it references each spec by §-section rather than committing patch text. Rationale:

1. **Spec-drift risk.** Mid-soak the operator may refine a spec (e.g., MATCH-001 §5.1 option (a) vs (b)). Pre-staged `.patch` text becomes stale; a referenced spec stays current with whatever the operator finalizes.
2. **Mixed-shape patches.** OBS-005 + GOV-003 are small (2-3 line edits + marker removal — like Lever B). MATCH-001 + OBS-003 + EXEC-002 + Lever A.1 are larger (new methods, conditional branches, classifier additions). Pre-staging some-but-not-all `.patch` files creates an inconsistent operator surface.
3. **IC §15 Rule 4 applies (capture vs improvement separation).** Wave-1 deploys are improvement (intentional behavior changes), not capture. Per Rule 4 they land as separate reviewed commits at deploy time, with operator approval per change. Pre-staging full patch text bypasses the deploy-time review checkpoint.

**Lever B is the exception** (cycle-10 Codex authored `wave-3-deploy-hunks/lever-b.patch`) because the Lever B v1 LOCK addendum specifies a single 2-line threshold change with no shape ambiguity. Wave-1's larger specs do not have that property.

**Operator follow-up:** at Wave-1 deploy-day, follow `wave-1-commit-messages-prestaged.md` + each spec's §2/§5 hunk text. No `.patch` files needed.

## Blocker 3 — Wave-3 Lever C cross-series guard implementation: INTENTIONAL DEFERRAL (per spec lock)

**Codex status:** "...or Lever C implementation hunks. Those need exact deploy-code decisions or an approved source patch, not inference."

**Resolution:** Authoring the Lever C BlendTask cross-series guard is **explicitly out of scope per the v1 LOCK addendum**:

> §4 Acceptance criteria: "This addendum does NOT authorize the prod-code change. Wave-3 territory."
> — `docs/superpowers/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md`

The Lever C impl lands at Wave-3 deploy time (≥ 2026-06-17, only if Wave-2 stalls AND Branch D not fired) per `wave-2-wave-3-changelog-entries-prestaged.md` version sequence table.

**Harness already complete.** `tests/test_lever_c_cross_series_correlation.py` contains 7 strict-xfail cases + 1 positive control covering all 6 LOCK addendum §3 invariants:

| test | LOCK §3 case | status |
|---|---|---|
| `test_cross_series_correlation_window_config_knob_exists` | (parent) | xfail-strict |
| `test_blend_task_uses_cross_series_headline_in_window_reason_string` | §3 test 3 (reason string) | xfail-strict |
| `test_normalized_headline_hash_function_exists` | §3 test 2 (hash helper exists) | xfail-strict |
| `test_headline_hash_normalizes_parent_spec_section_3_2_variants` | §3 test 2 (cosmetic-variant collision) | xfail-strict |
| `test_cross_series_suppression_within_window_reason_path_exists` | §3 test 3 (within-window suppression) | xfail-strict |
| `test_cross_series_suppression_releases_after_window_contract_exists` | §3 test 4 (post-window release) | xfail-strict |
| `test_headline_hash_recorded_after_gate_pass_not_at_entry_contract_exists` | §3 test 5 (record-after-gate-pass) | xfail-strict |
| `test_cross_series_window_zero_disables_contract_exists` | §3 test 6 (window=0 disable) | xfail-strict |
| `test_existing_skipped_emission_reasons_unchanged_today` | positive control | passes today |

LOCK addendum §4 acceptance #1 is satisfied. No further pre-Wave-3 work needed on this front.

**Operator follow-up:** at Wave-3 commit 1 (Lever B) + commit 2 (Lever C) deploy time, implement Lever C per the LOCK addendum §2 pseudo-flow + parent spec §3.2 normalization regex. The 7 strict-xfail markers must be removed in the same hunk per the existing `wave-2-wave-3-changelog-entries-prestaged.md` Wave-3 block.

## Summary

| blocker | resolution | operator action |
|---|---|---|
| 1 — plist fixtures | UNBLOCKED (cycle-10.5 captured + audit passes) | none |
| 2 — Wave-1 6-commit hunks | intentional deferral (commit-messages doc + spec refs are the artifact) | follow commit-messages doc + specs at deploy day |
| 3 — Lever C impl | intentional deferral per LOCK §4 (Wave-3 territory) | implement at Wave-3 deploy time per LOCK addendum §2 + parent spec §3.2 |

## Cross-links

- `tests/fixtures/installed_plists/README.md` — fixture refresh procedure
- `scripts/launchd_template_equivalence_audit.py` — audit script
- `tests/test_launchd_plist_template_render.py` — `--fixtures` test now active
- `docs/governance/wave-1-commit-messages-prestaged.md` — Wave-1 deploy-day operator guidance
- `docs/superpowers/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` — Lever C LOCK
- `tests/test_lever_c_cross_series_correlation.py` — Lever C harness (already complete)
- `docs/IMPLEMENTATION_CONTRACT.md` §15 — production-config-capture invariants
