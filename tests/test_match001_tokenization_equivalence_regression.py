"""MATCH-001 (B') tokenization-equivalence regression test.

Pins Codex's 2026-05-03 empirical finding (commit `e5b7213`,
`docs/governance/2026-05-03-match001-tokenization-equivalence-audit.md`):

  - Substring containment over `ticker_lower`: 1,076 suppressed keys on
    13-day archive.
  - `_tokenize(ticker)` set-difference: 0 suppressed keys.
  - Symmetric difference: 1,076 (100% divergence).

The regression invariants pinned here are *archive-size-invariant*:

  1. `bprime_tokenize_setdiff_suppressed_keys` must remain 0 — the
     existing `_tokenize` keeps hyphens, so any non-empty headline
     overlap survives the set-difference. If a future refactor
     splits `_tokenize` on hyphens, this number jumps non-zero.
  2. `simulation_bprime_substring_suppressed_keys` must remain > 0 —
     the archive always contains some ticker-substring matches. If
     this drops to 0 the simulation's predicate has been silently
     inverted or the archive has lost MATCH_DIAGNOSTIC records.
  3. Symmetric difference > 0 — the two predicates DO diverge. If
     this drops to 0, both predicates have converged on the same
     output, which means either (a) `_tokenize` was changed to split
     on hyphens (would make set-diff agree with substring on tokens
     that are full ticker-strings, but most tokens aren't), or (b)
     the simulation's substring check was rewritten.

Together these three invariants catch any future change in either
`analysis/market_matcher.py:_tokenize` or
`scripts/simulations/match001_tokenization_equivalence_audit.py:
bprime_tokenize_setdiff_suppresses` that re-introduces the
divergence the spec §5.1 addendum warned against.

Codex's exact 2026-05-03 number (1,076) is *not* pinned because the
archive grows post-soak; only the bounded-or-zero invariants are
pinned. Reproducibility against the exact number stays in the audit
report `docs/governance/2026-05-03-match001-tokenization-equivalence-
audit.md` for the audit-trail.
"""

from __future__ import annotations

import pytest

from scripts.simulations.match001_tokenization_equivalence_audit import analyze


def _safe_analyze() -> dict | None:
    """Run the audit, returning None if the archive is empty (e.g., a fresh
    checkout where `logs/trades/archive/2026/04/` and `2026/05/` haven't
    been populated yet).
    """
    result = analyze()
    if result.get("match_diagnostic_total", 0) == 0:
        return None
    return result


def test_tokenize_setdiff_suppresses_zero_keys_under_existing_tokenize():
    """Invariant 1: `_tokenize(ticker)` set-difference produces 0 suppressed
    keys because `_tokenize` keeps hyphens (single-token ticker shape).
    Catches any future refactor of `_tokenize` to split on hyphens."""
    result = _safe_analyze()
    if result is None:
        pytest.skip("archive empty; skipping regression invariant")
    assert result["bprime_tokenize_setdiff_suppressed_keys"] == 0, (
        "tokenize-setdiff suppressed keys must be 0 under the existing "
        "`_tokenize` (which keeps hyphens). Codex's 2026-05-03 audit found "
        "this empirically; if this assertion fires, `_tokenize` may have "
        "been refactored to split on hyphens, which would re-introduce the "
        "spec §2 set-difference math but break headline / market-title "
        "tokenization elsewhere. See "
        "`docs/superpowers/specs/2026-05-03-match-001-token-guard-refinement-design.md` "
        "§5.1 for the gotcha."
    )


def test_substring_suppresses_at_least_one_key():
    """Invariant 2: substring containment produces > 0 suppressed keys.
    Catches a regression where the simulation's predicate is silently
    inverted or the archive has lost MATCH_DIAGNOSTIC records."""
    result = _safe_analyze()
    if result is None:
        pytest.skip("archive empty; skipping regression invariant")
    assert result["simulation_bprime_substring_suppressed_keys"] > 0, (
        "substring suppressed keys must be > 0; Codex's 2026-05-03 audit "
        "found 1,076 on the 13-day archive. If this hit 0 the simulation "
        "predicate has been inverted or the archive has been truncated."
    )


def test_predicates_diverge_with_nonzero_symmetric_difference():
    """Invariant 3: symmetric difference > 0 — the two predicates produce
    different output. If this drops to 0, both predicates have converged."""
    result = _safe_analyze()
    if result is None:
        pytest.skip("archive empty; skipping regression invariant")
    sym_diff = result["simulation_vs_tokenize_setdiff_symmetric_diff"]
    assert sym_diff > 0, (
        "symmetric difference between substring and tokenize-setdiff "
        "predicates must be > 0; the two are designed to diverge per the "
        "MATCH-001 (B') §5.1 implementation gotcha. Codex's 2026-05-03 "
        "audit measured 1,076 (100% divergence). Convergence to 0 means "
        "one of the predicates has changed shape."
    )


def test_substring_strictly_dominates_setdiff():
    """Invariant 4: every key the tokenize-setdiff predicate suppresses
    should also be suppressed by substring containment (because being in
    `ticker_tokens` ⊆ `ticker_lower`-substring). The reverse is not true.
    `tokenize_setdiff_minus_simulation` should therefore be 0 (or very
    close to it — whitespace-edge-case tokens may diverge by ≤ 5 keys)."""
    result = _safe_analyze()
    if result is None:
        pytest.skip("archive empty; skipping regression invariant")
    setdiff_only = result["tokenize_setdiff_minus_simulation"]
    assert setdiff_only <= 5, (
        f"tokenize-setdiff suppresses {setdiff_only} keys that substring "
        "does not — substring should strictly dominate. > 5 indicates "
        "the predicates have crossed wires."
    )