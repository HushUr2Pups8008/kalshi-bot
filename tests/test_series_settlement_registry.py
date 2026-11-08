"""Registry eligibility is the only estimator gate. Proposals cannot trade."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.local_triage_shadow import run_shadow
from tests.test_local_triage import (
    FILTERS,
    NOW,
    ROOT,
    _Book,
    _Pages,
    _classifier,
    _envelope,
    _market,
    _response,
)
from utils.local_triage import DOMAINS, shadow_mechanism_guess
from utils.local_triage_classifier import parse_registry_proposal
from utils.series_settlement_registry import (
    DOMAINS as REGISTRY_DOMAINS,
    ESTIMABLE_MECHANISMS,
    MECHANISMS,
    coverage,
    disposition,
    estimator_eligible,
    registry_rows,
)

FIXTURE = ROOT / "tests" / "fixtures" / "series_settlement_mechanics.json"
_RAIN_PRIMARY = (
    "If the total precipitation at CLIPIT in Pittsburgh is strictly greater than 0 inches, "
    "then the market resolves to Yes."
)
_RAIN_BOILERPLATE = "missing daily precipitation values are counted as 0 inches."


def _fixtures() -> list[dict[str, Any]]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema"] == "curated_source_mechanic_fixtures_v1"
    assert payload["not_independent_human_gold"] is True
    rows = payload["rows"]
    assert isinstance(rows, list)
    return rows


def test_curated_source_mechanic_fixtures_match_the_registry() -> None:
    """These fixtures were written with the registry. They are not independent gold."""
    rows = _fixtures()
    assert len(rows) >= 20
    book = registry_rows()
    for fixture in rows:
        series = fixture["series_ticker"]
        row = book[series]
        assert row["domain"] == fixture["domain"]
        assert row["settlement_mechanism"] == fixture["expected_mechanism"]
        assert row["status"] == fixture["expected_status"]
        assert row["source_labels"] == fixture["source_labels"]
        assert fixture["official_mechanic"]
        assert fixture["rationale"]
        assert "probability" not in json.dumps(fixture).lower()
    assert book["KXRAIN"]["settlement_mechanism"] == "published_value_lookup"
    assert "https://weather.com/kalshi" in book["KXRAIN"]["source_urls"]


def test_weather_counted_boilerplate_is_not_an_event_count() -> None:
    """Precipitation boilerplate that says values are counted is not an event count."""
    guess = shadow_mechanism_guess(_RAIN_PRIMARY, _RAIN_BOILERPLATE)
    assert guess == "published_value_lookup"
    assert guess != "event_occurrence_count"
    assert shadow_mechanism_guess("missing daily precipitation values are counted as 0 inches.", "") != (
        "event_occurrence_count"
    )
    portwatch = (
        "If the number of transit calls as reported by the IMF PortWatch is above 25, "
        "the total is calculated by summing daily counts."
    )
    assert shadow_mechanism_guess(portwatch) == "published_value_lookup"


def test_only_promoted_registry_rows_are_estimator_eligible() -> None:
    """Keyword output, model output, and proposals cannot admit a series."""
    assert REGISTRY_DOMAINS == DOMAINS
    assert "event_occurrence_count" in MECHANISMS
    assert "other" not in ESTIMABLE_MECHANISMS
    assert estimator_eligible("KXAAAGASW", market_sources=["AAA"]) is True
    assert estimator_eligible(
        "KXAAAGASW",
        market_sources=["AAA"],
        keyword_settlement="countable",
        model_settlement="event_occurrence_count",
        proposal={"status": "promoted", "estimable": True, "settlement_mechanism": "opinion"},
    ) is True
    assert estimator_eligible(
        "NOT-A-SERIES",
        market_sources=["AAA"],
        keyword_settlement="lookup",
        model_settlement="published_value_lookup",
        proposal={"status": "promoted", "settlement_mechanism": "published_value_lookup"},
    ) is False
    assert estimator_eligible("KXHEGSETHOUT", market_sources=["official government records and registries"]) is False
    assert disposition("KXHEGSETHOUT", ["official government records and registries"]) == "unpromoted"
    assert estimator_eligible("KXDWTSELIMINATION", market_sources=["ABC"]) is False
    assert disposition("KXDWTSELIMINATION", ["ABC"]) == "ambiguous"
    assert estimator_eligible("KXCLAUDE", market_sources=["Anthropic"]) is False
    assert disposition("KXCLAUDE", ["Anthropic"]) == "registry_hit"
    assert estimator_eligible("KXAAAGASW", market_sources=[]) is False
    assert disposition("KXAAAGASW", []) == "ambiguous"
    stale = registry_rows()
    stale["KXAAAGASW"] = {**stale["KXAAAGASW"], "stale": True}
    assert disposition("KXAAAGASW", ["AAA"], registry=stale) == "ambiguous"
    assert estimator_eligible("KXAAAGASW", market_sources=["AAA"], registry=stale) is False
    conflict = registry_rows()
    conflict["KXWTI"] = {**conflict["KXWTI"], "conflict": True, "status": "unpromoted"}
    assert disposition("KXWTI", ["ICE"], registry=conflict) == "ambiguous"
    counted = {
        "series_ticker": "KXRAIN",
        "settlement_sources": ["The Weather Company"],
        "deterministic_settlement": "countable",
        "local_settlement": "countable",
    }
    assert coverage([counted])["estimator_eligible"] == 1
    assert coverage([counted])["registry_hit"] == 1
    assert coverage([{"series_ticker": "KXMISSING", "settlement_sources": ["AAA"]}])["registry_miss"] == 1


def test_registry_proposals_are_unpromoted_and_not_read_by_admission(tmp_path: Path) -> None:
    """A proposal file is shadow output. Admission helpers do not consume it."""
    proposal = {
        "domain": "macro",
        "settlement_mechanism": "published_value_lookup",
        "source_candidate": "AAA",
        "rules_summary": "The rule reads a published price.",
        "rationale": "Draft only.",
        "ambiguity": "none",
    }
    parsed, category = parse_registry_proposal(json.dumps(proposal))
    assert category == ""
    assert parsed is not None
    rejected, failure = parse_registry_proposal(json.dumps({**proposal, "probability": 0.5}))
    assert rejected is None
    assert failure == "probability_field"

    seen: list[dict[str, Any]] = []

    def _open(request: Any, timeout: float = 0) -> Any:
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        seen.append(body)
        if body["response_format"]["json_schema"]["name"] == "registry_proposal":
            return _response(_envelope(proposal))(request)
        return _response(
            _envelope(
                {
                    "domain": "politics",
                    "settlement": "countable",
                    "settlement_source": "Factbase",
                    "rationale": "The rules count posts.",
                    "ambiguity": "none",
                }
            )
        )(request)

    funnel = run_shadow(
        _Pages([_market(series_ticker="KXAAAGASW", rules_primary="Resolves according to AAA.")], []),
        now=NOW,
        out_dir=tmp_path / "local_triage",
        filters=FILTERS,
        classifier=_classifier(_open),
        sports_prefixes=(),
        orderbook_for=lambda _ticker: _Book(None, None),
        market_for=lambda _ticker: None,
        max_classify=50,
        write_proposals=True,
    )
    path = Path(funnel["paths"]["registry_proposals"])
    written = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert written
    assert all(row["status"] == "unpromoted" and row["estimable"] is False for row in written)
    assert any(body["response_format"]["json_schema"]["name"] == "registry_proposal" for body in seen)
    registry_source = (ROOT / "utils" / "series_settlement_registry.py").read_text(encoding="utf-8")
    assert "registry-proposals" not in registry_source
    assert "place_limit_order" not in registry_source
    assert "/v1/orders" not in registry_source
    assert "api.x.ai" not in registry_source
    for folder in ("trading", "tasks"):
        for source in (ROOT / folder).rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            assert "registry-proposals" not in text
            assert "estimator_eligible" not in text
            assert "series_settlement_registry" not in text
