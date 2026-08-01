from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from analysis import research_gate as research_gate_module
from analysis.research_gate import ResearchEvidence, ResearchQuery


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _strict_gdp_market(**overrides: object) -> SimpleNamespace:
    payload = {
        "ticker": "KXGDP-26JUL30-T2.0",
        "title": "Will real GDP increase by more than 2.0% in Q2 2026?",
        "subtitle": "",
        "rules_primary": (
            "This market resolves Yes only if the BEA seasonally adjusted annual "
            "rate (SAAR) of real GDP growth for Q2 2026 is greater than 2.0%."
        ),
        "rules_secondary": "The BEA Advance Estimate for Q2 2026 is the settlement source.",
        "settlement_sources": (),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _strict_contract():
    contract = research_gate_module._parse_gdp_threshold_contract(_strict_gdp_market())
    assert contract is not None
    return contract


def _gdpnow_query() -> ResearchQuery:
    return ResearchQuery(
        query="GDPNow real GDP growth outlook for Q2 2026",
        query_intent="base_rate",
        source_class="specialized_data",
    )


def _fred_gdpnow_observation(contract, **overrides: object) -> ResearchEvidence:
    observed_at = _now_iso()
    payload = {
        "source_class": "specialized_data",
        "source_name": "FRED GDPNow",
        "source_url": "https://fred.stlouisfed.org/series/GDPNOW",
        "title": "FRED GDPNow forecast for Q2 2026",
        "snippet": "FRED GDPNow real GDP growth estimate for Q2 2026 is 1.1889% SAAR.",
        "claim_type": "base_rate",
        "supports_direction": "neutral",
        "supports_confidence": 0.3,
        "published_at": observed_at,
        "retrieved_at": observed_at,
        "metric_name": "gdpnow_real_gdp_growth_saar",
        "metric_value": 1.1889,
        "metric_unit": "percent_saar",
        "extraction_confidence": 0.95,
        "contract_fingerprint": contract.contract_fingerprint,
    }
    payload.update(overrides)
    return ResearchEvidence(**payload)


def test_gdp_threshold_contract_accepts_only_strict_real_gdp_saar_threshold() -> None:
    market = _strict_gdp_market()

    contract = research_gate_module._parse_gdp_threshold_contract(market)

    assert contract is not None
    assert contract.metric_name == "gdpnow_real_gdp_growth_saar"
    assert contract.metric_unit == "percent_saar"
    assert contract.comparator == ">"
    assert contract.threshold == pytest.approx(2.0)
    assert contract.target_quarter == 2
    assert contract.target_year == 2026
    assert contract.contract_fingerprint == research_gate_module._contract_fingerprint(market)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {
                "rules_primary": "This market resolves Yes if Q2 2026 real GDP SAAR is at least 2.0%.",
            },
            id="at-least",
        ),
        pytest.param(
            {
                "rules_primary": "This market resolves Yes if Q2 2026 real GDP SAAR is less than 2.0%.",
            },
            id="less-than",
        ),
        pytest.param(
            {
                "rules_primary": "This market resolves Yes if Q2 2026 real GDP SAAR is < 2.0%.",
            },
            id="symbolic-less-than",
        ),
        pytest.param(
            {
                "rules_primary": (
                    "This market resolves Yes only if real GDP is greater than "
                    "2.0% in Q2 2026. CPI is reported at a seasonally adjusted "
                    "annual rate."
                ),
            },
            id="mixed-unbound-saar",
        ),
        pytest.param(
            {
                "title": "Will real GDP increase by more than 2 in Q2 2026?",
            },
            id="unbound-verbal-comparator",
        ),
        pytest.param(
            {
                "rules_primary": (
                    "This market resolves Yes only if Q2 2026 real GDP SAAR is "
                    "greater than 2.0%. A conflicting real GDP SAAR condition is "
                    "> 3.0% in Q2 2026."
                ),
            },
            id="symbolic-greater-than",
        ),
        pytest.param(
            {
                "rules_primary": (
                    "This market resolves Yes only if Q2 2026 real GDP SAAR is "
                    "greater than 2.0%. A conflicting real GDP SAAR condition is "
                    "＞ 3.0% in Q2 2026."
                ),
            },
            id="unicode-greater-than",
        ),
        pytest.param(
            {
                "rules_primary": (
                    "This market resolves Yes only if Q2 2026 real GDP SAAR is "
                    "greater than 2.0%. Another clause defines a 1.0%–2.0% GDP "
                    "range."
                ),
            },
            id="unicode-range",
        ),
        pytest.param(
            {
                "title": "Will Q2 2026 real GDP growth fall in the 1.0% to 2.0% bucket?",
                "rules_primary": "Settlement uses the Q2 2026 real GDP SAAR bucket.",
            },
            id="range",
        ),
        pytest.param(
            {
                "title": "Will the economic market resolve Yes?",
                "rules_primary": "The published settlement measurement resolves the market.",
                "rules_secondary": "",
            },
            id="ticker-only",
        ),
        pytest.param(
            {
                "title": "Will real GDP increase by more than 2.0%?",
                "rules_primary": "This market resolves Yes only if real GDP SAAR is greater than 2.0%.",
                "rules_secondary": "",
            },
            id="missing-period",
        ),
        pytest.param(
            {
                "rules_primary": "This market resolves Yes only if Q2 2026 real GDP SAAR is greater than 3.0%.",
            },
            id="conflicting-threshold",
        ),
        pytest.param(
            {
                "title": "Will real GDP increase by more than 2.0% in Q2 2026?",
                "rules_primary": "This market resolves Yes only if Q2 2026 real GDP year-over-year growth is greater than 2.0%.",
            },
            id="non-saar",
        ),
    ],
)
def test_gdp_threshold_contract_rejects_ambiguous_or_unsupported_terms(
    overrides: dict[str, object],
) -> None:
    assert research_gate_module._parse_gdp_threshold_contract(
        _strict_gdp_market(**overrides)
    ) is None


def test_current_run_gdpnow_context_binds_verified_observation_to_exact_contract_and_query() -> None:
    contract = _strict_contract()
    query = _gdpnow_query()
    observation = _fred_gdpnow_observation(contract)

    context = research_gate_module._build_current_run_gdpnow_observation_context(
        observation,
        query=query,
        contract=contract,
    )

    assert context is not None
    assert context.query == query.query
    assert context.contract_fingerprint == contract.contract_fingerprint
    assert context.source_url == observation.source_url
    assert context.source_observation_date == observation.published_at
    assert context.retrieved_at == observation.retrieved_at
    assert context.metric_value == observation.metric_value
    assert context.metric_unit == observation.metric_unit
    assert context.extraction_confidence == observation.extraction_confidence
    assert research_gate_module._gdpnow_context_matches_observation(
        context,
        observation,
        query=query,
        contract=contract,
    )
    assert not research_gate_module._gdpnow_context_matches_observation(
        context,
        replace(observation, metric_value=1.5),
        query=query,
        contract=contract,
    )


@pytest.mark.parametrize(
    "observation_overrides,query",
    [
        pytest.param(
            {"source_url": "https://spoof.invalid/series/GDPNOW"},
            _gdpnow_query(),
            id="spoofed-provenance",
        ),
        pytest.param(
            {"metric_value": float("nan")},
            _gdpnow_query(),
            id="nonfinite-value",
        ),
        pytest.param(
            {"extraction_confidence": 0.1},
            _gdpnow_query(),
            id="low-extraction-confidence",
        ),
        pytest.param(
            {"metric_unit": "percent_change"},
            _gdpnow_query(),
            id="wrong-metric-unit",
        ),
        pytest.param(
            {"published_at": 123},
            _gdpnow_query(),
            id="malformed-published-at",
        ),
        pytest.param(
            {
                "published_at": (datetime.now(timezone.utc) - timedelta(days=365)).isoformat().replace("+00:00", "Z"),
                "retrieved_at": (datetime.now(timezone.utc) - timedelta(days=365)).isoformat().replace("+00:00", "Z"),
            },
            _gdpnow_query(),
            id="stale-observation",
        ),
        pytest.param(
            {
                "published_at": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat().replace("+00:00", "Z"),
                "retrieved_at": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat().replace("+00:00", "Z"),
            },
            _gdpnow_query(),
            id="future-observation",
        ),
        pytest.param(
            {"contract_fingerprint": "wrong-contract"},
            _gdpnow_query(),
            id="wrong-contract-fingerprint",
        ),
        pytest.param(
            {},
            ResearchQuery(
                query="General economic outlook",
                query_intent="base_rate",
                source_class="specialized_data",
            ),
            id="missing-gdp-query-context",
        ),
    ],
)
def test_current_run_gdpnow_context_fails_closed_for_invalid_input(
    observation_overrides: dict[str, object],
    query: ResearchQuery,
) -> None:
    contract = _strict_contract()
    observation = _fred_gdpnow_observation(contract, **observation_overrides)

    assert research_gate_module._build_current_run_gdpnow_observation_context(
        observation,
        query=query,
        contract=contract,
    ) is None


def test_gdpnow_strict_mismatch_requires_opposite_non_equal_side() -> None:
    contract = _strict_contract()

    assert research_gate_module._gdpnow_strictly_mismatches_provisional_side(
        1.99,
        contract=contract,
        provisional_side="yes",
    )
    assert research_gate_module._gdpnow_strictly_mismatches_provisional_side(
        2.01,
        contract=contract,
        provisional_side="no",
    )
    assert not research_gate_module._gdpnow_strictly_mismatches_provisional_side(
        2.0,
        contract=contract,
        provisional_side="yes",
    )
    assert not research_gate_module._gdpnow_strictly_mismatches_provisional_side(
        1.99,
        contract=contract,
        provisional_side="no",
    )
    assert not research_gate_module._gdpnow_strictly_mismatches_provisional_side(
        2.01,
        contract=contract,
        provisional_side="neutral",
    )


def test_gdpnow_provisional_side_requires_independent_fresh_support_and_positive_edge() -> None:
    contract = _strict_contract()
    observed_at = _now_iso()
    raw_gdpnow = _fred_gdpnow_observation(
        contract,
        supports_direction="yes",
        supports_confidence=0.95,
    )
    independent_support = ResearchEvidence(
        source_class="reputable_secondary",
        source_name="Reuters",
        source_url="https://www.reuters.com/markets/us/q2-2026-real-gdp-outlook",
        title="Economists expect Q2 2026 real GDP growth to clear 2.0%",
        snippet="Independent reporting supports the Q2 2026 real GDP YES case.",
        claim_type="supporting",
        supports_direction="yes",
        supports_confidence=0.82,
        retrieved_at=observed_at,
        contract_fingerprint=contract.contract_fingerprint,
    )
    kwargs = {
        "provisional_side": "yes",
        "contract": contract,
        "yes_ask": 0.47,
        "no_ask": 0.54,
        "estimated_probability_yes": 0.70,
        "contract_ticker": "",
        "queries": (),
    }

    assert research_gate_module._gdpnow_provisional_side_is_independently_justified(
        evidence=[raw_gdpnow, independent_support],
        **kwargs,
    )
    assert not research_gate_module._gdpnow_provisional_side_is_independently_justified(
        evidence=[raw_gdpnow],
        **kwargs,
    )
    assert not research_gate_module._gdpnow_provisional_side_is_independently_justified(
        evidence=[raw_gdpnow, independent_support],
        yes_ask=None,
        **{key: value for key, value in kwargs.items() if key != "yes_ask"},
    )
    assert not research_gate_module._gdpnow_provisional_side_is_independently_justified(
        evidence=[raw_gdpnow, independent_support],
        estimated_probability_yes=0.48,
        **{key: value for key, value in kwargs.items() if key != "estimated_probability_yes"},
    )
    assert not research_gate_module._gdpnow_provisional_side_is_independently_justified(
        evidence=[raw_gdpnow, independent_support],
        live_mode=True,
        **kwargs,
    )
    for probability_yes in (float("nan"), float("inf"), float("-inf")):
        assert not research_gate_module._gdpnow_provisional_side_is_independently_justified(
            evidence=[raw_gdpnow, independent_support],
            estimated_probability_yes=probability_yes,
            **{
                key: value
                for key, value in kwargs.items()
                if key != "estimated_probability_yes"
            },
        )
    for yes_ask in (float("nan"), float("inf"), float("-inf")):
        assert not research_gate_module._gdpnow_provisional_side_is_independently_justified(
            evidence=[raw_gdpnow, independent_support],
            yes_ask=yes_ask,
            **{key: value for key, value in kwargs.items() if key != "yes_ask"},
        )
