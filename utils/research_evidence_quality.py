"""Shared source-identity and diversity policy for decision-grade research."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


OFFICIAL_RESEARCH_SOURCE_CLASSES = frozenset(
    {
        "official",
        "official_primary",
        "official_source",
        "resolution_source",
        "rules_source",
    }
)

STRUCTURED_OFFICIAL_RESEARCH_METRICS = frozenset(
    {
        "cpi_monthly_change_single_decimal",
        "gdpnow_real_gdp_growth_saar",
        "nws_daily_high_temp_f",
    }
)

STRUCTURED_OFFICIAL_SETTLEMENT_CLAIM_TYPES = frozenset(
    {
        "corroboration",
        "official_resolution",
        "resolution",
        "settlement",
        "settlement_source",
        "supporting",
    }
)

_COUNTRY_CODE_SECOND_LEVEL_LABELS = frozenset({"ac", "co", "com", "edu", "gov", "net", "org"})
_SPEECH_CONTRACT_ACTIVE_PATTERN = re.compile(
    r"\bif\s+(?P<subject>.{1,100}?)\s+"
    r"(?:says?|mentions?|utters?|uses?)\s+"
    r"(?P<phrase>.{1,160}?)\s+"
    r"(?:as\s+part\s+of|during|at|before|by|,?\s*then\b|[.;])",
    flags=re.I,
)
_SPEECH_CONTRACT_PASSIVE_PATTERN = re.compile(
    r"\bif\s+(?P<phrase>.{1,100}?)\s+(?:is|are)\s+"
    r"(?:said|mentioned|uttered|used)\s+by\s+"
    r"(?P<subject>.{1,80}?)\s+"
    r"(?:as\s+part\s+of|during|at|before|by|,?\s*then\b|[.;])",
    flags=re.I,
)
_SPEECH_RELATION_TERMS = frozenset(
    {
        "mention",
        "mentioned",
        "mentions",
        "message",
        "preview",
        "previews",
        "quote",
        "quoted",
        "remarks",
        "repeat",
        "repeated",
        "repeats",
        "said",
        "say",
        "says",
        "saying",
        "speech",
        "stated",
        "statement",
        "told",
        "transcript",
        "uttered",
        "use",
        "used",
        "uses",
        "using",
        "wording",
        "words",
    }
)
_SPEECH_SUBJECT_STOPWORDS = frozenset(
    {"current", "market", "president", "resolves", "resolution", "that", "then", "will"}
)
_CONTRACT_RELEVANCE_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "against",
        "base",
        "before",
        "broad",
        "candidate",
        "check",
        "confirmed",
        "contract",
        "context",
        "contradiction",
        "counter",
        "current",
        "decision",
        "denied",
        "disconfirming",
        "during",
        "evidence",
        "false",
        "frequency",
        "historical",
        "kalshi",
        "latest",
        "market",
        "objection",
        "official",
        "opponent",
        "price",
        "primary",
        "prior",
        "rate",
        "reputable",
        "result",
        "resolution",
        "resolves",
        "rules",
        "search",
        "secondary",
        "status",
        "source",
        "staleness",
        "supporting",
        "that",
        "then",
        "this",
        "update",
        "what",
        "when",
        "where",
        "which",
        "will",
        "with",
    }
)
MIN_DIRECTIONAL_SUPPORT_CONFIDENCE = 0.6
MIN_COUNTER_EVIDENCE_CONFIDENCE = 0.2


@dataclass(frozen=True)
class SpeechContractSpec:
    detected: bool
    phrases: tuple[str, ...]
    subject_terms: tuple[str, ...]


@dataclass(frozen=True)
class ContractRelevanceSpec:
    speech: SpeechContractSpec
    terms: tuple[str, ...]

    @property
    def detected(self) -> bool:
        return self.speech.detected or len(self.terms) >= 4


def research_source_key(
    source_class: object,
    source_name: object,
    source_url: object,
) -> str:
    """Return a normalized independent-source identity, strongest field first."""
    url = str(source_url or "").strip()
    if url:
        try:
            hostname = urlparse(url).hostname
        except ValueError:
            hostname = None
        host = _organization_domain(hostname)
        if host:
            return host
    name = str(source_name or "").strip().lower()
    if name:
        return name
    return ""


def has_reliable_research_source_path(
    evidence: Collection[object],
) -> bool:
    """Derive source diversity and the narrow structured-official exception."""
    if any(_is_structured_official_signal(item) for item in evidence):
        return True
    normalized_keys = {
        source_key
        for item in evidence
        if (
            source_key := research_source_key(
                _field(item, "source_class"),
                _field(item, "source_name"),
                _field(item, "source_url"),
            )
        )
        and source_key != "google.com"
    }
    normalized_classes = _normalized_nonempty(
        [_effective_source_class(item) for item in evidence]
    )
    return (
        len(normalized_keys) >= 2
        and len(normalized_classes) >= 2
        and bool(normalized_classes & OFFICIAL_RESEARCH_SOURCE_CLASSES)
    )


def extract_speech_contract_resolution_phrases(
    ticker: object,
    texts: Collection[object],
) -> tuple[str, ...]:
    """Extract explicit phrase alternatives from speech/mention contract rules."""
    return build_speech_contract_spec(ticker, texts).phrases


def build_speech_contract_spec(
    ticker: object,
    texts: Collection[object],
) -> SpeechContractSpec:
    """Build fail-closed speech-contract identity from ticker and rule/query text."""
    cleaned_texts = [str(value or "").strip() for value in texts if str(value or "").strip()]
    combined = " ".join(cleaned_texts).lower()
    ticker_text = str(ticker or "").strip().lower()
    detected = "mention" in ticker_text or any(
        pattern.search(combined) is not None
        for pattern in (
            re.compile(r"\bwhat\s+will\b.{1,100}\bsay\s+during\b", flags=re.I),
            re.compile(
                r"\b(?:resolves?|resolution)\s+yes\s+if\b.{0,180}"
                r"(?:says?|mentions?|utters?|uses?|uttered|mentioned)\b",
                flags=re.I,
            ),
        )
    )
    if not detected:
        return SpeechContractSpec(False, (), ())
    phrases: list[str] = []
    subject_terms: set[str] = set()
    seen: set[str] = set()
    for text in cleaned_texts:
        for pattern in (
            _SPEECH_CONTRACT_ACTIVE_PATTERN,
            _SPEECH_CONTRACT_PASSIVE_PATTERN,
        ):
            for match in pattern.finditer(text):
                subject_terms.update(
                    token
                    for token in _normalized_phrase_text(match.group("subject")).split()
                    if len(token) >= 4 and token not in _SPEECH_SUBJECT_STOPWORDS
                )
                for raw_phrase in re.split(
                    r"\s*/\s*|\s+or\s+",
                    match.group("phrase"),
                    flags=re.I,
                ):
                    phrase = re.sub(r"^[\s\"'`]+|[\s\"'`,;:.]+$", "", raw_phrase)
                    phrase = re.sub(r"\s+", " ", phrase).strip()
                    key = phrase.lower()
                    if not phrase or len(phrase) > 100 or key in seen:
                        continue
                    seen.add(key)
                    phrases.append(phrase)
    return SpeechContractSpec(
        True,
        tuple(phrases),
        tuple(sorted(subject_terms)),
    )


def evidence_is_relevant_to_speech_contract(
    text: object,
    spec: SpeechContractSpec,
) -> bool:
    """Require phrase, speaker, and speech relation for directional evidence."""
    if not spec.detected:
        return True
    if not spec.phrases or not text_mentions_speech_contract_resolution_phrase(
        text,
        spec.phrases,
    ):
        return False
    tokens = set(_normalized_phrase_text(text).split())
    if not tokens & _SPEECH_RELATION_TERMS:
        return False
    return not spec.subject_terms or set(spec.subject_terms) <= tokens


def build_contract_relevance_spec(
    ticker: object,
    texts: Collection[object],
) -> ContractRelevanceSpec:
    """Build deterministic contract terms for directional evidence checks."""
    cleaned_texts = [str(value or "").strip() for value in texts if str(value or "").strip()]
    speech = build_speech_contract_spec(ticker, cleaned_texts)
    terms = {
        token
        for token in _normalized_phrase_text(" ".join(cleaned_texts)).split()
        if token.isalpha()
        and len(token) >= 4
        and not token.startswith("kx")
        and token not in _CONTRACT_RELEVANCE_STOPWORDS
    }
    return ContractRelevanceSpec(speech=speech, terms=tuple(sorted(terms)))


def evidence_is_relevant_to_contract(
    text: object,
    spec: ContractRelevanceSpec,
) -> bool:
    """Fail closed when directional evidence lacks contract-condition overlap."""
    if spec.speech.detected:
        return evidence_is_relevant_to_speech_contract(text, spec.speech)
    if not spec.detected:
        return True
    evidence_terms = {
        token
        for token in _normalized_phrase_text(text).split()
        if token.isalpha()
        and len(token) >= 4
        and token not in _CONTRACT_RELEVANCE_STOPWORDS
    }
    if not evidence_terms:
        return False
    overlap = evidence_terms & set(spec.terms)
    return len(overlap) >= 2


def text_mentions_speech_contract_resolution_phrase(
    text: object,
    phrases: Collection[object],
) -> bool:
    """Require evidence to contain a complete speech-contract phrase alternative."""
    normalized_text = _normalized_phrase_text(text)
    if not normalized_text:
        return False
    text_tokens = set(normalized_text.split())
    for value in phrases:
        normalized_phrase = _normalized_phrase_text(value)
        if not normalized_phrase:
            continue
        phrase_tokens = normalized_phrase.split()
        if len(phrase_tokens) == 1:
            if phrase_tokens[0] in text_tokens:
                return True
            continue
        if normalized_phrase in normalized_text:
            return True
    return False


def _normalized_phrase_text(value: object) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _normalized_nonempty(values: Collection[str]) -> set[str]:
    return {str(value or "").strip().lower() for value in values if str(value or "").strip()}


def _effective_source_class(evidence: object) -> str:
    source_class = str(_field(evidence, "source_class") or "").strip().lower()
    if source_class != "rules_source":
        return source_class
    source_url = str(_field(evidence, "source_url") or "").strip()
    try:
        hostname = str(urlparse(source_url).hostname or "").lower().strip(".")
    except ValueError:
        hostname = ""
    trusted_url = (
        hostname == "kalshi.com"
        or hostname.endswith(".kalshi.com")
        or hostname == "kalshi-public-docs.s3.amazonaws.com"
    )
    return "rules_source" if trusted_url else "other"


def effective_research_source_class(evidence: object) -> str:
    """Return source class after validating provenance-sensitive classifications."""
    return _effective_source_class(evidence)


def _organization_domain(hostname: str | None) -> str:
    host = str(hostname or "").strip().lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return host
    suffix_size = 2
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _COUNTRY_CODE_SECOND_LEVEL_LABELS:
        suffix_size = 3
    return ".".join(labels[-suffix_size:])


def _is_structured_official_signal(evidence: object) -> bool:
    source_class = _effective_source_class(evidence)
    claim_type = str(_field(evidence, "claim_type") or "").strip().lower()
    direction = str(_field(evidence, "supports_direction") or "").strip().lower()
    metric_name = str(_field(evidence, "metric_name") or "").strip()
    confidence = _as_float(_field(evidence, "supports_confidence"))
    extraction_confidence = _as_float(_field(evidence, "extraction_confidence"))
    metric_value = _field(evidence, "metric_value")
    has_metric_value = metric_value is not None and (not isinstance(metric_value, str) or bool(metric_value.strip()))
    return (
        source_class in OFFICIAL_RESEARCH_SOURCE_CLASSES
        and claim_type in STRUCTURED_OFFICIAL_SETTLEMENT_CLAIM_TYPES
        and direction in {"yes", "no"}
        and confidence >= 0.6
        and metric_name in STRUCTURED_OFFICIAL_RESEARCH_METRICS
        and (has_metric_value or extraction_confidence >= 0.8)
    )


def _field(evidence: object, name: str) -> Any:
    if isinstance(evidence, Mapping):
        return evidence.get(name)
    return getattr(evidence, name, None)


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
