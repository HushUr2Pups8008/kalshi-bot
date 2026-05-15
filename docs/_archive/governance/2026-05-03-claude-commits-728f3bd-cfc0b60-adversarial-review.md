# Adversarial review — Claude commits `728f3bd … cfc0b60`

**Reviewer:** Claude (executing Codex's #1 task; Codex usage exhausted, so no second-pair-of-eyes today).
**Drafted:** 2026-05-03 (post-snapshot-5)
**Commits in scope (oldest → newest):**

1. `728f3bd` docs(governance): mid-soak Phase 2 snapshot 4 — liveness check
2. `d61da2d` test(match-001): tokenization-equivalence regression
3. `406a7d6` test(bothealth): pin SKIPPED_LOG_OVERRIDE env-var support — Codex F2 follow-up
4. `356a35c` docs(spec): pre-load EDGE-004 Lever A.1+ feed-onboarding
5. `cfc0b60` docs(governance): pre-stage post-soak-close operator rehearsal checklist

This review applies the same adversarial lens Codex has used on prior Claude batches. Self-review is weaker than peer review by design — items here are ones I noticed only on a second-pass read. The probable-blind-spot caveat applies.

## Severity legend

- **Blocker:** ship-stops the spec or tests will mislead operators if not fixed.
- **Watch:** not blocking but should be tracked / fixed when convenient.
- **Note:** observation only, no action expected.

## Findings

### F1 — `test_substring_strictly_dominates_setdiff` mathematical lemma is mis-stated (Watch)

`tests/test_match001_tokenization_equivalence_regression.py:108-122`. The docstring claims:

> every key the tokenize-setdiff predicate suppresses should also be suppressed by substring containment (because being in `ticker_tokens` ⊆ `ticker_lower`-substring)

This is not a clean ⊆ relation. `ticker_tokens` are the *result* of tokenizing the **headline / market title**, not the ticker. The predicates compare `headline_tokens` against `ticker_tokens` (set-difference) versus `ticker_lower` against `headline_text` (substring containment). They look at different surfaces. The empirical claim "substring strictly dominates" stands (Codex's audit), but the docstring's set-theoretic argument is post-hoc rationalisation, not a proof.

**Fix:** soften the docstring to "Codex's 2026-05-03 audit empirically found set-diff suppressions ⊆ substring suppressions on the 13-day archive; this invariant pins that empirical relationship, not a mathematical one." Otherwise a future reader trusts the lemma and writes a regression that breaks it.

### F2 — `test_bothealth_script_honours_skipped_log_override_env_var` weaker than docstring claims (Watch)

`tests/test_obs003_skipped_stream_synthesis.py:204+` (commit `406a7d6`). The docstring says the test pins the env var is *used*, "not just declared." The `used_with_default` check accepts either `${SKIPPED_LOG_OVERRIDE:-...}` or the raw quoted form `"$SKIPPED_LOG_OVERRIDE"`. The raw quoted form would be present even if the script just `echo`s it for diagnostics — which is "use" in a literal sense but not the path-source use the harness needs.

**Fix at landing time:** when bothealth.sh is updated, add the assertion that `SKIPPED_LOG_OVERRIDE` appears on the same line as `cat`/`grep`/`<` redirection. Today's source-inspection contract is fine as a placeholder; the stronger subprocess-style integration test is correctly deferred.

### F3 — Snapshot 4 escalation criterion was met retroactively but no acknowledgement in snapshot 5 cross-link (Note)

`docs/governance/2026-05-03-mid-soak-snapshot-4.md` (commit `728f3bd`) defined an escalation criterion: if the next cycle had not landed by 22:00Z, run launchd inspection. The cycle landed at 21:29:40Z (33 min before the deadline) — criterion did NOT fire.

Snapshot 5 (`c02dc87`) does mention this. But the cross-link is one-way: snapshot 4 is not edited to add a closing note like "RESOLVED: cycle landed 21:29:40Z, criterion did not fire." A future operator reading snapshot 4 in isolation could think the escalation was outstanding.

**Fix:** add a 1-line "RESOLVED" footer to snapshot 4 referring to snapshot 5. Cheap; closes the audit loop. (Counter-argument: snapshots are point-in-time documents and should not be retroactively edited. Defensible to leave alone.)

### F4 — Snapshot 4 is launchd-only operator-runbook content, references are macOS-specific (Note)

`docs/governance/2026-05-03-mid-soak-snapshot-4.md` (commit `728f3bd`) escalation block uses `launchctl` and `sudo log show` — both macOS-only. The PROFIT-PHASE2-001 soak is running on the Mac instance (per project gotchas: "concurrent Mac + Windows instances on the same network trigger Reddit 403s"), so this is correct for the operational reality, but the global portability rule (`~/.claude/rules/portability.md`) prefers cross-platform default. A Windows-side operator reading the snapshot would not know what to do.

**Action:** none (this is an incident-response snapshot for the actual host running the soak; operator-runbook content gets a pass on the cross-platform rule). Note for the rollback runbook: it does carry both macOS and Windows variants and is the correct pattern.

### F5 — A.1+ spec §3.1 lists URLs without ranking among them; checklist §7 inherits the gap (Watch)

`docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md` §3.1 (commit `356a35c`) lists 5 candidate URLs (`warontherocks.com`, `csis.org`, `understandingwar.org`, `cfr.org`, `atlanticcouncil.org`) without specifying a deploy order or empirical ranking among them. Codex's candidate-feed sizing audit (`docs/_archive/governance/2026-05-03-lever-a1-plus-candidate-feed-sizing.md`) ranked the *class*, not individual sources within the class.

This gap propagates to the rehearsal checklist (commit `cfc0b60`) §7, which says "verify each candidate feed URL" without specifying which to deploy first.

**Action:** Codex task #3 (specific-source ranking within specialist-analyst) is the right next deliverable; this is exactly the residual gap. Spec is OK to land today on the basis "any of these 5 is empirically defensible; the operator picks the live one at deploy time," but expect a §3.1 update once Codex's per-source audit lands.

**Codex left this task incomplete (usage exhausted).** Filing as immediate Claude-side todo: write the per-source ranking audit ourselves before A.1+1 deploys. This is task #3 in the cycle.

### F6 — Rehearsal checklist §6.1 cites stale test count (Watch)

`docs/governance/post-soak-close-rehearsal-checklist.md` §6.1 (commit `cfc0b60`):

> Re-run `pytest tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1 -q`. Confirm 6 strict-xfail + 1 positive control.

After commit `87c3f15` (Lever A.1+ analysis-class harness, landed in the same cycle), the test class `TestSourceClassClassifierLeverA1` is unchanged but a sibling class `TestSourceClassClassifierLeverA1PlusAnalysisBranch` now exists with 6 more strict-xfail tests. The checklist §7 (Lever A.1+) does NOT reference this new harness.

**Fix:** §7.1 (A.1+ pre-deploy check) should add `pytest tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1PlusAnalysisBranch -q` and `pytest tests/test_lever_a1plus_feed_config.py -q` so the post-deploy validation includes them. This is a doc-maintenance follow-up.

### F7 — `_safe_analyze()` recomputes the full archive scan per test (Note)

`tests/test_match001_tokenization_equivalence_regression.py` calls `_safe_analyze()` from each of its 4 tests. The audit's `analyze()` reads the full `MATCH_DIAGNOSTIC` archive every call — 4× the I/O of a single fixture. Test runtime today is fine (sub-second), but if the archive grows to gigabytes the cost compounds.

**Action:** none today. If runtime exceeds a second, switch to a `@pytest.fixture(scope="module")` cached call. Filing as a future-perf note only.

### F8 — Spec §3.1 / `understandingwar.org` aliasing inconsistent with feed-config harness allow-list (Note)

`tests/test_lever_a1plus_feed_config.py:_CANDIDATE_SPECIALIST_ANALYST_URLS` (commit `91937c8`, sibling cycle) lists `understandingwar.org` (ISW). The A.1+ spec §3.1 lists "Optional: ISW, CFR, Atlantic Council" without the URL. A future reader matching the spec to the test could miss that ISW = `understandingwar.org`.

**Action:** add a parenthetical `(understandingwar.org)` next to "ISW" in spec §3.1. Trivial.

## Severity summary

| ID | severity | area |
|---|---|---|
| F1 | Watch | tokenization-regression docstring (math is post-hoc) |
| F2 | Watch | bothealth env-var test (weaker than claimed) |
| F3 | Note | snapshot-4 audit-loop closure |
| F4 | Note | snapshot-4 macOS-specific (operator pass) |
| F5 | Watch | A.1+ spec lacks per-source ranking — Codex task #3 closes this |
| F6 | Watch | rehearsal checklist §6/§7 inherits stale-test-count gap |
| F7 | Note | regression test perf scaling |
| F8 | Note | spec/test ISW aliasing inconsistency |

No Blockers. 4 Watches + 4 Notes. None of the 5 commits should be reverted; F5 + F6 are best closed in a follow-up doc-maintenance commit pairing the per-source audit (task #3) with checklist §6/§7 + spec §3.1 updates.

## Self-review caveat

This is Claude reviewing Claude's own output. Real adversarial review by an independent agent would catch issues I am blind to. The probable false-negative rate for self-review is ≥ 30 % per common literature. When Codex usage refreshes, this review should be re-run independently and the differences logged.
