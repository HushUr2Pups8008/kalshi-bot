"""Strict-xfail harness for MATCH-001 (B') tokenization option (b).

Spec: docs/superpowers/specs/2026-05-03-match-001-tokenization-option-b-design.md

Status: option (a) substring containment is the recommended fix per the
parent MATCH-001 spec §5.1. This harness pre-stages option (b) — a
hyphen-splitting `_tokenize_ticker` helper — for the case where the
implementer rejects option (a) at landing time. If option (a) lands,
this harness becomes dead code (the strict-xfail markers continue to
fail because `_tokenize_ticker` is never created, and CI continues to
pass because xfailed = expected).

If the entire option (b) path is formally closed, this file should be
removed as part of the closure commit (analogous to Lever E's pattern).
Until then it documents the alternative implementation contract.
"""

from __future__ import annotations

import pytest


_OPTION_B_XFAIL_REASON = (
    "MATCH-001 (B') option (b) — hyphen-splitting `_tokenize_ticker` helper — "
    "not implemented. Option (a) substring containment is the recommended path. "
    "This xfail trips strictly only if option (b) lands; if option (a) lands "
    "instead, the helper is never created and these tests continue to xfail."
)


def _matcher_source() -> str:
    import inspect
    import analysis.market_matcher as _mm
    return inspect.getsource(_mm)


@pytest.mark.xfail(reason=_OPTION_B_XFAIL_REASON, strict=True)
def test_tokenize_ticker_helper_exists_in_matcher():
    """Pin that `_tokenize_ticker` is exported from analysis/market_matcher
    if option (b) lands. Source-inspection contract — does not depend on
    the helper's exact internal regex."""
    src = _matcher_source()
    assert "def _tokenize_ticker" in src, (
        "option (b) landing requires a `_tokenize_ticker` helper in "
        "analysis/market_matcher.py. If option (a) landed instead, this "
        "xfail-strict trip is expected and the harness is dead code per spec §8."
    )


@pytest.mark.xfail(reason=_OPTION_B_XFAIL_REASON, strict=True)
@pytest.mark.parametrize(
    "ticker,expected",
    [
        ("KXTRUMPIRAN-26MAY01", {"kxtrumpiran", "26may01"}),
        ("KXFISAEXTEND-26APR-MAY01", {"kxfisaextend", "26apr", "may01"}),
        ("KXMOCTRUMP25-26-APR24", {"kxmoctrump25", "26", "apr24"}),
        ("KXSBUDGETRES-26APR-APR28", {"kxsbudgetres", "26apr", "apr28"}),
        ("KXVANCEPAKISTAN-26APR21-APR25", {"kxvancepakistan", "26apr21", "apr25"}),
    ],
    ids=["TRUMPIRAN", "FISAEXTEND", "MOCTRUMP25", "SBUDGETRES", "VANCEPAKISTAN"],
)
def test_tokenize_ticker_splits_on_hyphens_and_lowercases(ticker: str, expected: set[str]):
    """Pin the hyphen-and-whitespace split semantics. Each canonical Kalshi
    ticker shape must tokenize to its components."""
    from analysis.market_matcher import _tokenize_ticker  # type: ignore[attr-defined]
    assert _tokenize_ticker(ticker) == expected