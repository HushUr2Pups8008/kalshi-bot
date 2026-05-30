"""Single-source guard for the geopolitical named-entity set.

The political-figure / country roster (trump, vance, putin, zelensky, …) is the
part most prone to rot: when an administration changes, the names change. It was
duplicated in analysis/market_matcher and analysis/market_specificity (the latter
an explicit dated "snapshot"), so a roster update in one could silently diverge
from the other. These tests pin a single canonical source so the two consumers
can never drift, and prove the extraction is behavior-preserving.
"""

import analysis.market_matcher as mm
import analysis.market_specificity as ms
from analysis.geo_entities import GEO_NAMED_ENTITIES

# Institutional tokens deliberately kept out of the matcher gate set (adding them
# there could change gate behavior); they live only in the specificity sub-score.
SPECIFICITY_EXTRAS = frozenset({"iaea", "un", "scotus", "fbi", "cia", "opec", "who"})


def test_matcher_uses_canonical_geo_entities():
    assert mm._GEO_NAMED_ENTITIES == GEO_NAMED_ENTITIES


def test_specificity_extends_canonical_with_local_extras():
    assert ms._GEO_NAMED_ENTITIES == GEO_NAMED_ENTITIES | SPECIFICITY_EXTRAS


def test_single_source_prevents_figure_drift():
    # Figures live in exactly one place, so an administration turnover updates
    # both consumers at once instead of drifting between two hand-kept copies.
    for figure in ("trump", "vance", "putin", "zelensky", "netanyahu"):
        assert figure in GEO_NAMED_ENTITIES
    # The specificity-only institutional tokens must NOT leak into the matcher's
    # gate set (that was the documented reason they were kept local).
    assert not (SPECIFICITY_EXTRAS & mm._GEO_NAMED_ENTITIES)
