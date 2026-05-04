"""Pre-load harness for Lever A.1+1.5 evidence_scorer `legal=0.65` weight.

Spec: docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md §3.2
Status: pre-loaded during PROFIT-PHASE2-001 soak; lands as part of
A.1+1.5 deploy (Wave 2, post-soak day 14+; option-B parallel to
option-A specialist-analyst). Adds a `legal` weight to
`evidence_scorer._SOURCE_CLASS_QUALITY`.

Spec §3.2 recommends `0.65`. Current dict (analysis/evidence_scorer.py:24):

    "official": 0.85
    "news":     0.70
    "analysis": 0.60
    "market":   0.55

The spec text said "between `analysis=0.60` and `official=0.75`" — the
`official=0.75` reference was incorrect (actual value is 0.85). The
0.65 value still makes sense: it places `legal` between `analysis` and
`news`, capturing the rationale that legal/regulatory analysts have
slightly stronger primary-source proximity than general analysis but
less than mainstream news wires (which carry primary-source statements
verbatim). The value is tunable at deploy time per operator judgement.
"""

from __future__ import annotations

import pytest


_LEGAL_WEIGHT_XFAIL_REASON = (
    "Lever A.1+1.5 evidence_scorer `legal=0.65` weight not yet added. "
    "The A.1+1.5 deploy adds an entry to "
    "`analysis/evidence_scorer.py:_SOURCE_CLASS_QUALITY` per spec §3.2. "
    "Trips on the deploy commit; remove the marker in the same hunk."
)


@pytest.mark.xfail(reason=_LEGAL_WEIGHT_XFAIL_REASON, strict=True)
def test_legal_class_weight_set_to_spec_value():
    """Pin the post-A.1+1.5 outcome that `_SOURCE_CLASS_QUALITY['legal']`
    is added with the spec-recommended value of 0.65. The exact value is
    operator-tunable at deploy time, but `0.65` is the design-time
    reference; if a different value is chosen, this test must be updated
    to reflect the deploy-time decision."""
    from analysis.evidence_scorer import _SOURCE_CLASS_QUALITY
    assert "legal" in _SOURCE_CLASS_QUALITY, (
        "`_SOURCE_CLASS_QUALITY` missing `legal` entry. A.1+1.5 spec §3.2 "
        "calls for adding a `legal` source-class quality weight."
    )
    assert _SOURCE_CLASS_QUALITY["legal"] == pytest.approx(0.65, abs=0.001), (
        f"`_SOURCE_CLASS_QUALITY['legal']` is "
        f"{_SOURCE_CLASS_QUALITY.get('legal')!r}; spec §3.2 recommends 0.65. "
        f"If the deploy chose a different value, update this test to match."
    )


def test_existing_class_weights_unchanged_today():
    """Positive control: the existing four source-class weights remain
    in `_SOURCE_CLASS_QUALITY` and at their current values. Catches a
    regression where the A.1+1.5 deploy accidentally re-tunes existing
    weights while adding the `legal` entry. The 4 existing weights are
    load-bearing for OPP / PAPER_TRADE conversion across all classes
    that already have evidence in the archive."""
    from analysis.evidence_scorer import _SOURCE_CLASS_QUALITY
    expected = {
        "official": 0.85,
        "news":     0.70,
        "analysis": 0.60,
        "market":   0.55,
    }
    for cls, val in expected.items():
        assert cls in _SOURCE_CLASS_QUALITY, (
            f"`_SOURCE_CLASS_QUALITY` missing `{cls}`; weight dict has been altered."
        )
        assert _SOURCE_CLASS_QUALITY[cls] == pytest.approx(val, abs=0.001), (
            f"`_SOURCE_CLASS_QUALITY[{cls!r}]` is "
            f"{_SOURCE_CLASS_QUALITY[cls]!r}; expected {val}. "
            f"A.1+1.5 deploy must not re-tune existing weights."
        )


def test_legal_weight_relative_position_constraint():
    """Positive control: WHEN `legal` is added, its weight must sit
    BETWEEN `analysis=0.60` and `news=0.70` (the spec §3.2 rationale
    is that legal has slightly stronger primary-source proximity than
    generic analysis but less than mainstream news wires). Today
    `legal` is not present so this passes vacuously; after deploy this
    pins the relative ordering even if the operator picks a value
    other than 0.65 (e.g., 0.62 or 0.68 — still acceptable; 0.55 or
    0.75 would not be)."""
    from analysis.evidence_scorer import _SOURCE_CLASS_QUALITY
    if "legal" not in _SOURCE_CLASS_QUALITY:
        pytest.skip("`legal` not yet in dict; vacuously satisfied")
    legal = _SOURCE_CLASS_QUALITY["legal"]
    analysis = _SOURCE_CLASS_QUALITY["analysis"]
    news = _SOURCE_CLASS_QUALITY["news"]
    assert analysis < legal < news, (
        f"`legal={legal}` must sit strictly between `analysis={analysis}` "
        f"and `news={news}` per A.1+1.5 spec §3.2. Operator may pick any "
        f"value in that interval; values outside it require a spec revision."
    )
