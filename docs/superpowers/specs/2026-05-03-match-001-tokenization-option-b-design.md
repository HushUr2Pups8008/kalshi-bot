# PROFIT-MATCH-001 (B') — tokenization option (b) hyphen-splitting helper

**Status:** design (alternative resolution to the tokenization gotcha documented in `2026-05-03-match-001-token-guard-refinement-design.md` §5.1; **option (a) substring containment is the recommended path**. This spec exists so the implementer has a complete second option if (a) is rejected at landing time)
**Tracker:** `PROFIT-MATCH-001` (alternative implementation path)
**Owner:** Claude (design) + Codex (re-sizing if option (b) is chosen)
**Severity:** MED (only relevant if option (a) is rejected; otherwise this spec stays unimplemented)
**Drafted:** 2026-05-03

## 1. Why this spec exists

The MATCH-001 (B') spec proposed a `_meets_suppression_criteria` predicate using `non_ticker_overlap = overlap - _tokenize(market.ticker)`. Verified empirically that `_tokenize` keeps hyphens — `_tokenize("KXTRUMPIRAN-26MAY01") = {kxtrumpiran-26may01}` (single token) — so the set-difference produces ~0 keys flipped on the 13-day archive. Codex's tokenization-equivalence audit confirmed 100 % divergence between the spec's set-difference form and the simulation's substring form (1,076 keys vs 0 keys; commit `e5b7213`).

Option (a) — substring containment — is the recommended fix because it matches the simulation harness exactly and is the smaller diff. **This spec covers option (b)**: define a hyphen-splitting ticker tokenizer so the spec's set-difference math becomes valid as written.

If the implementer prefers option (a), close this spec as not-implemented and proceed with the substring-containment patch in MATCH-001 §5.1 option (a). If the implementer prefers option (b) (e.g., for stylistic reasons about set-vs-substring semantics), the components below are the implementation surface.

## 2. The fix (option (b))

### 2.1 New helper in `analysis/market_matcher.py`

```python
def _tokenize_ticker(ticker: str) -> set[str]:
    """Tokenize a Kalshi ticker by splitting on hyphens AND whitespace.

    Distinct from the existing `_tokenize` helper which preserves hyphens
    (kept that way because headlines / market titles use hyphens
    semantically). Tickers use hyphens as field separators (series prefix /
    contract date / variant), so for *ticker* purposes the right
    tokenization splits on `-`.

    Examples:
        _tokenize_ticker("KXTRUMPIRAN-26MAY01")     → {"kxtrumpiran", "26may01"}
        _tokenize_ticker("KXFISAEXTEND-26APR-MAY01") → {"kxfisaextend", "26apr", "may01"}
        _tokenize_ticker("KXMOCTRUMP25-26-APR24")    → {"kxmoctrump25", "26", "apr24"}
    """
    return {tok for tok in re.split(r"[-\s]+", ticker.lower()) if tok}
```

### 2.2 Use the new helper in the suppression predicate

```python
# analysis/market_matcher.py:_meets_suppression_criteria — option (b) form
ticker_tokens = _tokenize_ticker(market.ticker)
non_ticker_overlap = overlap - ticker_tokens
_has_supporting_non_ticker_token = bool(non_ticker_overlap)

_meets_suppression_criteria = (
    bool(heuristic_flags)
    and not _has_supporting_non_ticker_token
    and (_near_threshold_weak or _pure_single_entity)
)
```

### 2.3 Re-sized empirical surface required

The MATCH-001 sizing simulation (`scripts/simulations/match001_bprime_anchor_sizing.py`) currently uses substring containment. If option (b) lands, **the simulation harness must be updated to use `_tokenize_ticker` set-difference** so its 1,076-key estimate stays faithful to production.

Pre-deploy validation must include a re-run of the simulation harness against the new tokenizer. Expected:

- Substring containment over `ticker_lower`: produces 1,076 flips (current sizing).
- `_tokenize_ticker` set-difference: produces a *similar* count, because hyphen-split tokens like `kxtrumpiran` typically don't appear in headlines either, so most overlap tokens are still absent from `ticker_tokens`. **But the count may differ by a small amount** if any headline overlap token happens to equal a hyphen-split ticker fragment (e.g., ticker `KXTRUMP-...` and headline overlap including just `kxtrump` → set-difference catches it; substring also catches it; should match). Concrete edge cases: rare-but-possible.

Codex must re-run the sizing audit if option (b) lands; sub-50-key drift between option (a) and option (b) is acceptable, > 50-key drift requires investigating which approach is empirically right.

## 3. Components touched (option (b))

If option (b) is chosen at landing time:

- `analysis/market_matcher.py`:
  - Add `_tokenize_ticker` helper near the existing `_tokenize` (around line ~134).
  - Update `_meets_suppression_criteria` block per §2.2.
- `scripts/simulations/match001_bprime_anchor_sizing.py`:
  - Replace `ticker_lower = _ticker(match).lower()` + `any(token not in ticker_lower for ...)` with `ticker_tokens = _tokenize_ticker(_ticker(match))` + `bool(overlap - ticker_tokens)`.
  - Re-run the audit; record the new flip count.
- `tests/test_market_matcher.py`:
  - Add unit tests for `_tokenize_ticker` against the canonical ticker shapes (KXTRUMPIRAN / KXFISAEXTEND / KXMOCTRUMP25 / KXSBUDGETRES / KXVANCEPAKISTAN). Same shape list as the EXEC-002 `_series_prefix` parametrization (in `tests/test_blend_task.py`).
  - Update `TestSuppressionTokenGuardMATCH001` source-inspection pins to recognize the new helper. The two existing strict-xfail tests pin `_has_supporting_non_ticker_token` as the post-fix marker; under option (b) that marker still applies, so no test refactor needed there.

## 4. Acceptance criteria (option (b))

- `_tokenize_ticker` helper present in `analysis/market_matcher.py`.
- `_meets_suppression_criteria` uses `set-difference against _tokenize_ticker(market.ticker)`.
- Codex's MATCH-001 anchor sizing audit re-run with `_tokenize_ticker` produces a flip count within ±50 keys of the 1,076 substring estimate.
- All canonical-event headlines remain unsuppressed under option (b) — same regression-anchor invariant as option (a).
- Full pytest suite green (existing harness + new `_tokenize_ticker` unit tests + updated simulation harness).

## 5. Risk

- **Set-difference semantics admit different edge cases than substring containment.** A headline overlap token like `"trump"` against ticker `"KXTRUMP-25A"`:
  - Substring: `"trump" in "kxtrump-25a"` → True → suppression blocked.
  - Tokenizer: `{"trump"} - {"kxtrump", "25a"}` → `{"trump"}` non-empty → has_supporting=True → suppression blocked.
  - Both block; same verdict.
  - But: headline overlap token `"kxtrump"` (rare) against ticker `"KXTRUMP-25A"`:
    - Substring: `"kxtrump" in "kxtrump-25a"` → True → suppression blocked.
    - Tokenizer: `{"kxtrump"} - {"kxtrump", "25a"}` → `{}` empty → has_supporting=False → suppression FIRES.
  - Different verdict — but only on the unusual case where the headline contains the literal ticker prefix as a token. Empirically rare; counted in the ±50-key drift band.
- **Tokenizer mistake risk.** Splitting on hyphens for tickers but keeping hyphens for headlines / market-titles is a *non-symmetric* tokenization rule. Future maintainers may not realize the asymmetry exists and refactor `_tokenize` to also split on hyphens — which would break the headline / title matcher elsewhere. Mitigate with a clear docstring on `_tokenize_ticker` explaining the asymmetry.
- **Simulation harness drift.** If `_tokenize_ticker` lands but the simulation harness is not updated, future MATCH-001-adjacent audits report a stale flip count. Mitigation: change the simulation in the same commit as the production code.
- **Soak invariant.** Same as the parent MATCH-001 spec — decision-path edit. Cannot land mid-soak. Wave 1 of post-soak per the existing landing-order schedule.

## 6. Rollback

Same as MATCH-001 §8 if option (b) is chosen. Plus: restore the simulation harness to its substring form. Both reverts in the same commit.

## 7. Why option (a) remains the recommendation

- **Smaller diff.** Option (a) is a 1-line change; option (b) is the helper + predicate update + simulation update.
- **Matches the audit.** Codex's 1,076-key flip estimate is the empirical anchor for MATCH-001's value; option (a) reproduces it exactly because the simulation already uses substring containment.
- **No new asymmetric-tokenization landmine.** Option (b) introduces a `_tokenize` vs `_tokenize_ticker` asymmetry that's a future-maintainer trap.
- **Symmetric inverse of pre-fix.** Option (a) cleanly inverts the existing `_token_not_in_ticker` predicate; option (b) introduces a new tokenization shape that didn't exist pre-fix.

The only argument for option (b) is stylistic — set-difference reads slightly cleaner than de-Morgan'd `not any(... not in ...)`. That stylistic preference does not justify the additional complexity.

## 8. xfail harness pre-load

A 5-test harness for option (b) is provided in `tests/test_match001_tokenization_option_b.py`:

- 4 strict-xfail tests pinning `_tokenize_ticker` against canonical Kalshi ticker shapes (only one strict-xfail-passes today because the helper does not yet exist; xpasses the day option (b) lands).
- 1 strict-xfail source-inspection test pinning that `_tokenize_ticker` exists in `analysis/market_matcher.py`.

If option (a) lands instead, this harness becomes dead code. **Do not remove it during the option (a) landing commit** — leave it in place as a future-options reference. The strict-xfail markers will continue to fail (the helper doesn't exist) and CI will continue to pass (xfailed = expected). Only remove this harness if the entire option (b) path is formally closed (analogous to Lever E's closure pattern).

## 9. Out of scope

- **Refactoring `_tokenize` itself to split on hyphens.** Different shape; would break headline / title tokenization elsewhere. Not this spec.
- **Splitting on more characters** (underscores, dots). The asymmetric `[-\s]+` split is sufficient for Kalshi's ticker shape.
- **Migrating other ticker-substring-check sites in the codebase** to use `_tokenize_ticker`. Out of scope; only the suppression predicate uses ticker tokenization. Other sites (e.g., portfolio lookups, cooldowns) reference tickers by exact-string equality, not tokenization.
- **`PROFIT-LLM-001`** signal-analyzer LLM unification. Outside MATCH-001 scope entirely.
