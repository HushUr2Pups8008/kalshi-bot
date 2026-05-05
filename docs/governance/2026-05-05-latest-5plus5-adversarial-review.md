# 2026-05-05 Latest 5+5 Adversarial Review

**Scope:** Claude `2f1e900`, `bcf4102`, `165790c` plus the prior early-close/doc batch, and Codex's five governance docs from the same cycle.

**Review stance:** operator-facing correctness. Focus areas: §8.5.1 addendum wording, gate-7 invariant strictness, early-close attestation completeness.

## Findings

### 1. Gate-7 invariant command is inconsistent across artifacts

**Severity:** Medium

`docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` tells the operator to run:

```bash
git log --oneline --since "2026-05-01T19:01Z" --until HEAD -- analysis/ tasks/ feeds/ governance/ executor/ main.py
```

Problems:

- `--until HEAD` is date syntax, not a revision boundary. This is at best confusing and at worst silently wrong depending on git's date parser.
- The attestation template uses a different path set and omits the dedicated script.
- `scripts/check_soak_invariant.sh` is stricter than the docs because it adds `kalshi/` and `config.py`, but the template does not make that script the canonical gate-7 command.

**Recommended fix:** make `scripts/check_soak_invariant.sh` the single canonical gate-7 command in both the runbook and attestation template. Remove `--until HEAD` from the manual fallback.

### 2. Gate-7 path scope is still narrower than the operational invariant

**Severity:** Low / Medium

The script covers:

```text
analysis/ tasks/ feeds/ governance/ executor/ kalshi/ main.py config.py
```

That is stronger than the template, but it still leaves an explicit operator judgment call for runtime-adjacent files such as launchd plists and selected runtime scripts. This may be intentional because the current soak invariant is "bot runtime unchanged" rather than "every operational file unchanged", but the script should say which excluded paths are out of scope and why.

**Recommended fix:** add an "out-of-scope but reviewed separately" note for `ops/launchd/` and runtime-invoked `scripts/` entries. Keep broad non-runtime docs/tests excluded.

### 3. Gate-6 remains high-friction until the manual-review tool lands

**Severity:** Medium

The attestation template is complete enough for manual entry, but the proposed `scripts/governance_decision_review.py` is not present in this checkout. Gate 6 still requires hand-reviewing the decision stream and calculating the reasonable-rate outside the repo.

**Recommended fix:** block early-close attestation on either the review tool landing or a committed CSV/JSON review ledger with denominator, numerator, reviewer, and disagreement set.

## Positive Checks

- §8.5.1 early-close wording preserves the no-runtime-change invariant.
- The attestation template has explicit gates, sign-off fields, and follow-on deploy constraint language.
- The next-soak section correctly keeps the 168 h evidence window out of the cadence-halving change.

## Verdict

Early-close doc set is directionally sound, but gate 7 should be normalized before operator use. Gate 6 is procedurally complete but not yet ergonomic enough for the 158+ decision review burden.
