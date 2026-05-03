# Adversarial Review: Claude Commits c5cbc6f through 90c26cf

**Reviewed:** 2026-05-03
**Scope:** `c5cbc6f`, `6cc1d6c`, `f699c10`, `9d08695`, `ae17b9c`, `bc06dc7`, `90c26cf`

## Findings

### F1 — MATCH-001 option (b) tokenizer does not recover the 1,076-key surface

**Severity:** HIGH
**Commit:** `ae17b9c`
**Files:** `docs/superpowers/specs/2026-05-03-match-001-tokenization-option-b-design.md:31`, `:66`; `tests/test_match001_tokenization_option_b.py:45`

The option (b) spec proposes `_tokenize_ticker("KXTRUMPIRAN-26MAY01") -> {"kxtrumpiran", "26may01"}` and then expects set-difference semantics to produce a flip count similar to substring containment. It will not. The archived overlap tokens are usually `trump` / `iran`, not `kxtrumpiran`, so `overlap - {"kxtrumpiran", "26may01"}` is still non-empty. That keeps `_has_supporting_non_ticker_token=True` and blocks suppression, which is the same failure mode as literal `_tokenize(ticker)`.

In other words, splitting only on hyphens does not decompose entity-prefix tickers into the entity tokens the predicate needs. Option (b) needs either substring containment anyway, a series-prefix parser that splits `KXTRUMPIRAN` into `trump`/`iran`, or a fresh sizing audit showing it works. The current option (b) harness pins the wrong tokenizer behavior.

**Fix:** mark option (b) as non-viable as written, or revise `_tokenize_ticker` to decompose known entity-prefix series before keeping it as a landing alternative.

### F2 — Snapshot 3 points operators at 2026-05-15 despite the reset hard ceiling

**Severity:** LOW
**Commit:** `c5cbc6f`
**File:** `docs/governance/2026-05-03-mid-soak-snapshot-3.md:13`

Snapshot 3 says "Continue to 2026-05-15." The debt log's reset note for `PROFIT-PHASE2-001` says the first valid decision after the `think=False` fix was `2026-05-02T04:12:53Z` and sets the new hard target close to `2026-05-16 ~04:12 UTC`.

This is an operator-facing date mismatch. It does not change the health read, but it can cause a premature close attempt.

**Fix:** use the reset hard ceiling date consistently, or explicitly distinguish original ETA from reset-window close.

## Non-Findings / Checks

- `6cc1d6c` and `90c26cf`: the Lever A.1 classifier harness/spec now correctly frame A.1 as prerequisite hygiene, not a standalone edge-production lever.
- `f699c10`: current-state landing-order header correctly captures MATCH-001 substring semantics, Lever E closure, Lever D demotion, and Lever C §3.2 empirics.
- `9d08695`: rollback runbook is procedural-only and does not touch runtime paths.
- `bc06dc7`: residual Lever C §3.1 references flagged in the previous review are fixed.
- Focused verification: `pytest tests/test_match001_tokenization_option_b.py tests/test_lever_a1_classifier_counterfactual.py tests/test_obs003_skipped_stream_synthesis.py -q` returned `13 passed, 14 xfailed`; ruff passed for the same files.
