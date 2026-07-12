from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from utils.research_evidence_quality import (
    STRUCTURED_OFFICIAL_RESEARCH_METRICS,
    build_contract_relevance_spec,
    build_speech_contract_spec,
    evidence_is_relevant_to_contract,
    has_reliable_research_source_path,
    research_evidence_temporally_valid,
    research_source_key,
)


def _evidence(
    source_class: str,
    *,
    url: str = "",
    name: str = "",
    claim_type: str = "supporting",
    direction: str = "yes",
    confidence: float = 0.9,
    metric_name: str | None = None,
    metric_value: float | None = None,
    extraction_confidence: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        source_class=source_class,
        source_url=url,
        source_name=name,
        claim_type=claim_type,
        supports_direction=direction,
        supports_confidence=confidence,
        metric_name=metric_name,
        metric_value=metric_value,
        extraction_confidence=extraction_confidence,
    )


def test_two_identities_from_one_source_class_are_not_diverse() -> None:
    evidence = [
        _evidence("official_primary", url="https://official.example/data"),
        _evidence("official_primary", url="https://agency.example/data"),
    ]

    assert has_reliable_research_source_path(evidence) is False


def test_two_identities_and_classes_with_official_source_are_reliable() -> None:
    evidence = [
        _evidence("resolution_source", url="https://official.example/data"),
        _evidence("reputable_secondary", url="https://wire.example/story"),
    ]

    assert has_reliable_research_source_path(evidence) is True


def test_untrusted_rules_publisher_does_not_create_official_source_path() -> None:
    evidence = [
        _evidence(
            "rules_source",
            url="https://www.indy100.com/politics/trump-quotes",
        ),
        _evidence(
            "reputable_secondary",
            url="https://usatoday.com/america-birthday",
        ),
    ]

    assert has_reliable_research_source_path(evidence) is False


def test_kalshi_rules_publisher_can_create_official_source_path() -> None:
    evidence = [
        _evidence(
            "rules_source",
            url="https://kalshi.com/markets/KXTRUMPMENTION",
        ),
        _evidence(
            "reputable_secondary",
            url="https://reuters.com/trump-maga",
        ),
    ]

    assert has_reliable_research_source_path(evidence) is True


def test_kalshi_source_name_cannot_override_untrusted_rules_url() -> None:
    evidence = [
        _evidence(
            "rules_source",
            url="https://www.indy100.com/politics/trump-quotes",
            name="Kalshi",
        ),
        _evidence(
            "reputable_secondary",
            url="https://usatoday.com/america-birthday",
        ),
    ]

    assert has_reliable_research_source_path(evidence) is False


def test_untrusted_rules_url_cannot_use_structured_official_exception() -> None:
    evidence = [
        _evidence(
            "rules_source",
            url="https://www.indy100.com/politics/trump-quotes",
            name="Kalshi",
            claim_type="official_resolution",
            direction="yes",
            confidence=0.9,
            metric_name="cpi_monthly_change_single_decimal",
            metric_value=0.3,
            extraction_confidence=0.95,
        )
    ]

    assert has_reliable_research_source_path(evidence) is False


def test_two_nonofficial_source_classes_are_not_reliable() -> None:
    evidence = [
        _evidence("reputable_secondary", url="https://wire.example/story"),
        _evidence("academic", url="https://journal.example/paper"),
    ]

    assert has_reliable_research_source_path(evidence) is False


@pytest.mark.parametrize("metric_name", sorted(STRUCTURED_OFFICIAL_RESEARCH_METRICS))
def test_valid_structured_official_signal_is_narrow_exception(metric_name: str) -> None:
    evidence = [
        _evidence(
            "official_primary",
            url="https://official.example/data",
            claim_type="official_resolution",
            direction="yes",
            confidence=0.6,
            metric_name=metric_name,
            metric_value=1.0,
        )
    ]

    assert has_reliable_research_source_path(evidence) is True


def test_structured_official_extraction_confidence_can_replace_metric_value() -> None:
    evidence = [
        _evidence(
            "official_primary",
            url="https://official.example/data",
            claim_type="settlement_source",
            direction="no",
            confidence=0.8,
            metric_name="nws_daily_high_temp_f",
            metric_value=None,
            extraction_confidence=0.8,
        )
    ]

    assert has_reliable_research_source_path(evidence) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_class": "reputable_secondary"},
        {"claim_type": "commentary"},
        {"direction": "neutral"},
        {"confidence": 0.59},
        {"metric_name": "generic_official_metric"},
        {"metric_value": None, "extraction_confidence": 0.79},
    ],
)
def test_invalid_structured_official_signal_is_not_exception(
    overrides: dict[str, object],
) -> None:
    fields: dict[str, object] = {
        "source_class": "official_primary",
        "claim_type": "official_resolution",
        "direction": "yes",
        "confidence": 0.9,
        "metric_name": "cpi_monthly_change_single_decimal",
        "metric_value": 0.3,
        "extraction_confidence": 0.95,
    }
    fields.update(overrides)
    evidence = [
        _evidence(
            str(fields["source_class"]),
            url="https://official.example/data",
            claim_type=str(fields["claim_type"]),
            direction=str(fields["direction"]),
            confidence=float(fields["confidence"]),
            metric_name=str(fields["metric_name"]),
            metric_value=fields["metric_value"],
            extraction_confidence=float(fields["extraction_confidence"]),
        )
    ]

    assert has_reliable_research_source_path(evidence) is False


def test_old_boolean_exception_api_is_impossible() -> None:
    with pytest.raises(TypeError):
        has_reliable_research_source_path(  # type: ignore[call-arg]
            [],
            has_structured_official_signal=True,
        )


def test_generic_single_official_source_is_not_exception() -> None:
    evidence = [
        _evidence("official_primary", url="https://official.example/data"),
    ]

    assert has_reliable_research_source_path(evidence) is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://WWW.Example.COM/path", "example.com"),
        ("https://api.bls.gov/data", "bls.gov"),
        ("https://www.bls.gov/data", "bls.gov"),
        ("https://foo.example.co.uk/data", "example.co.uk"),
        ("https://bar.example.co.uk/data", "example.co.uk"),
        ("https://user:pass@WWW.Api.BLS.GOV.:443/data", "bls.gov"),
    ],
)
def test_source_key_normalizes_url_to_organization_domain(
    url: str,
    expected: str,
) -> None:
    assert research_source_key("Official_Primary", "Named source", url) == expected


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("https://api.bls.gov/data", "https://www.bls.gov/data"),
        ("https://foo.example.co.uk/a", "https://bar.example.co.uk/b"),
    ],
)
def test_sibling_subdomains_do_not_create_fake_identity_diversity(
    left: str,
    right: str,
) -> None:
    evidence = [
        _evidence("official_primary", url=left),
        _evidence("reputable_secondary", url=right),
    ]

    assert has_reliable_research_source_path(evidence) is False


def test_source_key_falls_back_to_normalized_name_but_never_class() -> None:
    assert research_source_key("Official_Primary", " Named Source ", "") == "named source"
    assert research_source_key(" Official_Primary ", "", None) == ""


def test_empty_hostname_and_name_produce_no_identity() -> None:
    assert research_source_key("official_primary", "", "not-a-url") == ""
    assert research_source_key("official_primary", "", "https://[invalid") == ""
    evidence = [
        _evidence("official_primary"),
        _evidence("reputable_secondary", url="https://wire.example/story"),
    ]

    assert has_reliable_research_source_path(evidence) is False


def test_mapping_evidence_is_supported() -> None:
    evidence = [
        {
            "source_class": "resolution_source",
            "source_name": "Official",
            "source_url": "https://official.example/data",
        },
        {
            "source_class": "reputable_secondary",
            "source_name": "Wire",
            "source_url": "https://wire.example/story",
        },
    ]

    assert has_reliable_research_source_path(evidence) is True


def test_future_nws_report_date_is_temporally_invalid() -> None:
    evidence = {
        "metric_name": "nws_daily_high_temp_f",
        "published_at": "2026-07-11",
        "retrieved_at": "2026-07-10T14:11:00+00:00",
    }

    assert research_evidence_temporally_valid(evidence) is False


def test_nws_report_requires_prior_new_york_local_date() -> None:
    evidence = SimpleNamespace(
        metric_name="nws_daily_high_temp_f",
        published_at="2026-07-10",
        retrieved_at="2026-07-11T03:30:00+00:00",
    )

    assert research_evidence_temporally_valid(evidence) is False


def test_prior_day_nws_report_is_temporally_valid() -> None:
    evidence = SimpleNamespace(
        metric_name="nws_daily_high_temp_f",
        published_at="2026-07-10",
        retrieved_at="2026-07-11T14:00:00+00:00",
    )

    assert research_evidence_temporally_valid(evidence) is True


@pytest.mark.parametrize("metric_name", sorted(STRUCTURED_OFFICIAL_RESEARCH_METRICS))
def test_structured_official_metric_requires_retrieval_timestamp(metric_name: str) -> None:
    evidence = {
        "metric_name": metric_name,
        "metric_value": 1.0,
        "supports_direction": "yes",
    }

    assert research_evidence_temporally_valid(evidence) is False


def test_evidence_unavailable_as_of_validation_time_is_temporally_invalid() -> None:
    evidence = {
        "available_at": "2026-07-11T12:01:00Z",
        "retrieved_at": "2026-07-11T12:00:00Z",
    }

    assert (
        research_evidence_temporally_valid(
            evidence,
            as_of=datetime(2026, 7, 11, 12, tzinfo=timezone.utc),
        )
        is False
    )


@pytest.mark.parametrize(
    ("rules", "expected_phrase", "expected_subject"),
    [
        (
            "The market resolves Yes if Donald Trump says MAGA at the rally.",
            "MAGA",
            "trump",
        ),
        (
            "The market resolves Yes if Donald Trump mentions MAGA before midnight.",
            "MAGA",
            "trump",
        ),
        (
            "The market resolves Yes if MAGA is uttered by Donald Trump during the speech.",
            "MAGA",
            "trump",
        ),
    ],
)
def test_speech_contract_spec_extracts_supported_rule_syntaxes(
    rules: str,
    expected_phrase: str,
    expected_subject: str,
) -> None:
    spec = build_speech_contract_spec("KXTRUMPMENTION-26JUL24-MAGA", [rules])

    assert spec.detected is True
    assert expected_phrase in spec.phrases
    assert expected_subject in spec.subject_terms


def test_speech_contract_relevance_rejects_phrase_only_background_article() -> None:
    spec = build_contract_relevance_spec(
        "KXTRUMPMENTION-26JUL24-MAGA",
        ["The market resolves Yes if Donald Trump says MAGA during the speech."],
    )

    assert (
        evidence_is_relevant_to_contract(
            "Britannica explains the history and meaning of the MAGA movement under Trump.",
            spec,
        )
        is False
    )


def test_speech_contract_relevance_accepts_explicit_speech_evidence() -> None:
    spec = build_contract_relevance_spec(
        "KXTRUMPMENTION-26JUL24-MAGA",
        ["The market resolves Yes if Donald Trump says MAGA during the speech."],
    )


@pytest.mark.parametrize(
    "evidence_text",
    [
        "Ivanka Trump says MAGA during her remarks.",
        "Donald Duck mentioned MAGA during the event.",
        "Donald Trump says he will make changes. America is great again.",
    ],
)
def test_speech_contract_relevance_rejects_wrong_speaker_or_split_phrase(
    evidence_text: str,
) -> None:
    spec = build_contract_relevance_spec(
        "KXTRUMPMENTION-26JUL24-MAGA",
        [
            "The market resolves Yes if Donald Trump says Make America Great Again "
            "during the speech."
        ],
    )

    assert evidence_is_relevant_to_contract(evidence_text, spec) is False


def test_speech_contract_relevance_accepts_uses_relation() -> None:
    spec = build_contract_relevance_spec(
        "KXTRUMPMENTION-26JUL24-MAGA",
        ["The market resolves Yes if Donald Trump uses MAGA during the speech."],
    )

    assert (
        evidence_is_relevant_to_contract(
            "Donald Trump uses MAGA during his prepared remarks.",
            spec,
        )
        is True
    )

    assert (
        evidence_is_relevant_to_contract(
            "Transcript: Donald Trump said MAGA during his White House remarks.",
            spec,
        )
        is True
    )


def test_detected_mention_contract_without_phrase_fails_closed() -> None:
    spec = build_contract_relevance_spec(
        "KXTRUMPMENTION-26JUL24-MAGA",
        ["Official resolution rules are available from the market page."],
    )

    assert spec.speech.detected is True
    assert spec.speech.phrases == ()
    assert evidence_is_relevant_to_contract("Trump said MAGA in a speech.", spec) is False


def test_counter_query_boilerplate_is_not_contract_identity() -> None:
    spec = build_contract_relevance_spec(
        "KXUSTRDAGREEMENT-26JUL01",
        [
            "Will the US sign a trade agreement before July 1?",
            (
                "Will the US sign a trade agreement before July 1? evidence against "
                "YES evidence against NO false not confirmed denied opponent objection"
            ),
            "Will the US sign a trade agreement before July 1? latest update current status",
        ],
    )

    assert spec.detected is True
    assert (
        evidence_is_relevant_to_contract(
            "Opponent denied objection in an unrelated sports dispute.",
            spec,
        )
        is False
    )
    assert (
        evidence_is_relevant_to_contract(
            "Officials signed the bilateral trade agreement before July 1.",
            spec,
        )
        is True
    )
