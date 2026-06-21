---
name: replay-gate-analyze
description: Parse replay-CI gate artifacts and Rule 4 tables, surface EV deltas, and map findings to PROFIT-PHASE3-001 tier requirements. Use when reviewing a PR that touches scoring/blending/sizing/governance and needs replayed-EV evidence per IC §16.
---

# replay-gate-analyze

Decodes the output of `scripts/edge_replay/replay_gate.py` (~38K LOC) and `.github/workflows/replay-ci-gate.yml` runs so a reviewer can answer "does this diff have valid replayed-EV evidence?" without re-deriving the framework.

## When to invoke

- PR review where the diff touches `analysis/`, `governance/`, `trading/executor.py`, `tasks/blend_task.py`, `tasks/trade_readiness_gate.py`, or `tasks/calibration_*`.
- Operator question: "is this PR replay-clean?"
- Mid-cycle check on a long-running governance cycle.

Do NOT invoke for T0 PRs (mechanical / docs / tests-only) — gate auto-passes them; nothing to analyze.

## Tier reference

From `replay-ci-gate.yml` header:

| Tier | Scope | Evidence required |
|---|---|---|
| T0 | mechanical / docs / tests-only | auto-pass, none |
| T1 | paper-mode, replay-decidable | Rule 4 table + EV delta check |
| T2 | paper-mode, replay-indeterminate (prompt / LLM behavior) | Rule 4 + manual reviewer sign-off |
| T3 | live / sizing / runtime-infra | operator gate; replay informational only |

## Workflow

### Step 1 — Locate artifacts

```bash
# Most-recent gate run on this branch
gh run list --workflow replay-ci-gate.yml --branch "$(git branch --show-current)" --limit 5
gh run view <RUN_ID> --log | head -200

# Artifacts (Rule 4 table, EV deltas, scorer outputs)
gh run download <RUN_ID> -D /tmp/replay-artifacts/
ls /tmp/replay-artifacts/
```

If no gate run exists yet, run locally:

```bash
.venv/bin/python scripts/edge_replay/ci_entry.py --pr "$(gh pr view --json number -q .number)"
```

### Step 2 — Classify tier from diff

```bash
git diff --name-only origin/main...HEAD | sort -u
```

Map each path to its tier per the gate header. If ANY path is T3, the whole PR is T3 and replay is informational. Otherwise highest-tier wins.

### Step 3 — Parse Rule 4 table

Rule 4 = per-market replayed decision comparing pre-diff vs post-diff scorer output. Look for:

| Column | Meaning | Red flag |
|---|---|---|
| `n_markets` | sample size | < 30 → low statistical power, flag |
| `wr_before` / `wr_after` | win-rate | naive 50% framing is wrong — baseline is `Σ market_yes_price/100` per `[[feedback_market_implied_baseline]]` |
| `ev_before` / `ev_after` | expected-value | negative `ev_after` blocks T1 merge unless operator override |
| `coverage_change` | markets newly admitted/blocked | large swing → audit charter-label match per `[[feedback_audit_scorer_before_verdict]]` |
| `regime_confidence_dist` | rc histogram | mass below 0.20 → G4 fail-safe trips per CLAUDE.md readiness section |

### Step 4 — Cross-check against load-bearing constraints

Quick lookups before approving:

- **G1 / G4 chain**: did rc_dist shift mass below 0.20? Per CLAUDE.md `Readiness gate` section, G1 surfaces a G4 fail.
- **Anchor-rate polarity** (`governance/prompts.py:27-31`): if the diff touches prompts, confirm HIGH→DISABLE / LOW→KEEP block intact. PROFIT-GOV-002 regresses silently.
- **Status filter** (`analysis/market_matcher.py:440,490`): if diff touches market_matcher, confirm `status="open"` request filter not flipped to `"active"`. v0.30.0 broke this; 2726-error storm.
- **Same-signal guard** (`executor.py:218`): if diff touches executor, confirm guard still iterates ALL open trades for the ticker.

### Step 5 — Issue verdict

Output one of:

```
APPROVE — replay evidence clean, EV non-regressive, no load-bearing constraint touched.
APPROVE WITH CAVEAT — <list>. Operator should confirm <X> before merge.
BLOCK — <list>. Required before re-review: <Y>.
```

Always cite the gate run ID, tier classification, and which Rule 4 columns drove the call. Cite line ranges, not commit SHAs — SHAs rot at the next force-push.

## Anti-patterns

- Approving on "deploy ready" alone. Per `[[feedback_edge_priority_over_deploy_safety]]`, IC §16 requires **replayed-EV evidence**, not deploy readiness.
- Treating naive 50% win-rate as the baseline. Use market-implied baseline.
- Re-running the gate on a stale corpus. Confirm corpus seed timestamp matches the PR base.
- Accepting T2 PRs without manual sign-off. The gate does not auto-pass T2.
