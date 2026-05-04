# MATCH-001 B' spec-parity verification

**Author:** Codex
**Date:** 2026-05-04
**Purpose:** re-derive `bprime_suppresses` from the written spec, separate it from the current pre-B' runtime gate, and verify whether the 2026-05-03 false-suppression result depends on simulation drift

## TL;DR

No simulation drift found.

- The simulation predicate in `scripts/simulations/match001_bprime_anchor_sizing.py` matches the **corrected** spec form in `docs/superpowers/specs/2026-05-03-match-001-token-guard-refinement-design.md` section 5.1.
- The current runtime gate in `analysis/market_matcher.py` is **not** the B' predicate. It is the existing pre-B' suppression path, so direct equality is the wrong expectation.
- Replaying the corrected B' predicate over archived `MATCH_SUPPRESSED` still yields the same orthogonality result: `489/489` flip to kept.

## Corrected B' form

Spec section 5.1 rejects `_tokenize(ticker)` set-difference and replaces it with substring containment against the raw ticker text:

```python
ticker_lower = _ticker(match).lower()
has_supporting_non_ticker = any(token not in ticker_lower for token in overlap)
return (not has_supporting_non_ticker) and (near_threshold_weak or pure_single_entity)
```

That is the same form used by:

- `scripts/simulations/match001_bprime_anchor_sizing.py:bprime_suppresses`
- `scripts/simulations/match001_bprime_false_suppression_audit.py`

## What the current runtime does

Current runtime code at `analysis/market_matcher.py:626-648` computes:

```python
_token_not_in_ticker = not any(token in ticker_lower for token in overlap)
_meets_suppression_criteria = (
    bool(heuristic_flags)
    and _token_not_in_ticker
    and (_near_threshold_weak or _pure_single_entity)
)
```

That is the **existing** suppression gate:

- suppress when overlap tokens are **absent** from ticker text
- near-threshold / weak-structure path
- pre-B' behavior

The corrected B' spec does the opposite ticker-side test:

- suppress when overlap tokens are **only** ticker substrings
- single-anchor / no-support path
- additive post-fix behavior

## Archive replay

### A. Replay against the archived `MATCH_SUPPRESSED` stream

- archived `MATCH_SUPPRESSED` rows: `489`
- rows the corrected B' predicate would also suppress: `0`
- rows that flip to kept under corrected B': `489`

This reproduces the 2026-05-03 false-suppression audit exactly.

### B. Why this is not a contradiction

The archived `MATCH_SUPPRESSED` rows were produced by the old runtime gate, not by B'. They are mostly weak matches whose overlap token is **not** a ticker substring:

- `congress` matched into `KXBONDITESTIFY-26MAY-PBON`
- `russia` matched into `KXRUCRUDEX-26MAY13-T4.0`

Those should stay suppressed by the old gate and should **not** be suppressed by B'. That is the orthogonality finding, not drift.

## Bottom line

- The `489/489 flip` claim is still correct.
- The simulation is aligned with the corrected spec.
- The current runtime gate is a different predicate and should not be used as the truth source for B'.
- If MATCH-001 deploy code is written from the current runtime gate instead of the corrected spec section 5.1 form, that would be an implementation bug.

## Cross-links

- `docs/governance/2026-05-03-match001-bprime-false-suppression-audit.md`
- `docs/governance/2026-05-03-match001-bprime-anchor-sizing.md`
- `docs/superpowers/specs/2026-05-03-match-001-token-guard-refinement-design.md`
