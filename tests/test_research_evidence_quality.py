import pytest

from utils.research_evidence_quality import (
    STRUCTURED_OFFICIAL_RESEARCH_METRICS,
    has_reliable_research_source_path,
    research_source_key,
)


def test_two_identities_from_one_source_class_are_not_diverse() -> None:
    assert has_reliable_research_source_path(
        source_keys={"official.example", "agency.example"},
        source_classes={"official_primary"},
        has_structured_official_signal=False,
    ) is False


def test_two_identities_and_classes_with_official_source_are_reliable() -> None:
    assert has_reliable_research_source_path(
        source_keys={"official.example", "wire.example"},
        source_classes={"resolution_source", "reputable_secondary"},
        has_structured_official_signal=False,
    ) is True


def test_two_nonofficial_source_classes_are_not_reliable() -> None:
    assert has_reliable_research_source_path(
        source_keys={"wire.example", "journal.example"},
        source_classes={"reputable_secondary", "academic"},
        has_structured_official_signal=False,
    ) is False


@pytest.mark.parametrize("metric_name", sorted(STRUCTURED_OFFICIAL_RESEARCH_METRICS))
def test_caller_validated_structured_official_signal_is_narrow_exception(
    metric_name: str,
) -> None:
    assert metric_name
    assert has_reliable_research_source_path(
        source_keys={"official.example"},
        source_classes={"official_primary"},
        has_structured_official_signal=True,
    ) is True


def test_generic_single_official_source_is_not_exception() -> None:
    assert has_reliable_research_source_path(
        source_keys={"official.example"},
        source_classes={"official_primary"},
        has_structured_official_signal=False,
    ) is False


def test_source_key_prefers_normalized_url_domain() -> None:
    assert research_source_key(
        "Official_Primary",
        "Named source",
        "https://WWW.Example.COM/path",
    ) == "example.com"


def test_source_key_falls_back_to_normalized_name_then_class() -> None:
    assert research_source_key("Official_Primary", " Named Source ", "") == "named source"
    assert research_source_key(" Official_Primary ", "", None) == "official_primary"


def test_blank_source_identity_is_ignored() -> None:
    assert research_source_key("", "", "not-a-url") == ""
    assert has_reliable_research_source_path(
        source_keys={"", "  ", "official.example"},
        source_classes={"", "official_primary"},
        has_structured_official_signal=False,
    ) is False


def test_source_class_and_identity_normalization_prevents_fake_diversity() -> None:
    assert has_reliable_research_source_path(
        source_keys={"Example.com", " example.com "},
        source_classes={"Official_Primary", " official_primary "},
        has_structured_official_signal=False,
    ) is False
