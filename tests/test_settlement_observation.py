from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kalshi import KalshiMarket
from kalshi.settlement import normalize_kalshi_settlement
from polymarket.settlement import normalize_polymarket_settlement
from trading.settlement import (
    MarketOutcome,
    SettlementDriftError,
    SettlementObservation,
    SettlementValidationError,
    UnsupportedVoidError,
    VoidRefundContract,
    canonical_payload_json,
)
from trading.venue import MarketRef, Venue


OBSERVED_AT = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
EFFECTIVE_AT = datetime(2026, 7, 14, 17, 55, tzinfo=timezone.utc)
RULES_VERSION = "official-rules-v1"
SOURCE_ID = "official-api:test"


def _refund() -> VoidRefundContract:
    return VoidRefundContract(
        refund_cents_per_contract=Decimal("50"),
        refunds_entry_fee=False,
    )


def _kalshi_market(*, ticker: str = "KXTEST-26JUL", result: str = "yes") -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title="Test market",
        yes_bid=0.0,
        yes_ask=0.0,
        yes_price=0.0,
        volume=0,
        open_interest=0,
        close_time="2026-07-14T17:55:00+00:00",
        status="settled",
        result=result,
        updated_time=EFFECTIVE_AT,
        raw_payload_hash="a" * 64,
    )


def _kalshi_observation(
    *,
    result: str = "yes",
    previous_observation: SettlementObservation | None = None,
    supersedes_payload_sha256: str | None = None,
    void_refund: VoidRefundContract | None = None,
) -> SettlementObservation:
    return normalize_kalshi_settlement(
        MarketRef(Venue.KALSHI, "KXTEST-26JUL", "KXTEST-26JUL"),
        _kalshi_market(result=result),
        observed_at=OBSERVED_AT,
        effective_at=EFFECTIVE_AT,
        rules_version=RULES_VERSION,
        source_id=SOURCE_ID,
        void_refund=void_refund,
        previous_observation=previous_observation,
        supersedes_payload_sha256=supersedes_payload_sha256,
    )


def _polymarket_payload(outcome: str = "YES") -> dict[str, object]:
    return {
        "id": "pm-123",
        "slug": "will-example-happen",
        "settled": True,
        "resolvedOutcome": outcome,
    }


def _polymarket_observation(
    *,
    payload: dict[str, object] | None = None,
    previous_observation: SettlementObservation | None = None,
    supersedes_payload_sha256: str | None = None,
    void_refund: VoidRefundContract | None = None,
) -> SettlementObservation:
    return normalize_polymarket_settlement(
        MarketRef(Venue.POLYMARKET_US, "pm-123", "will-example-happen"),
        payload or _polymarket_payload(),
        observed_at=OBSERVED_AT,
        effective_at=EFFECTIVE_AT,
        rules_version=RULES_VERSION,
        source_id=SOURCE_ID,
        void_refund=void_refund,
        previous_observation=previous_observation,
        supersedes_payload_sha256=supersedes_payload_sha256,
    )


@pytest.mark.parametrize(
    ("venue", "raw_outcome", "expected"),
    [
        (Venue.KALSHI, "yes", MarketOutcome.YES),
        (Venue.KALSHI, "NO", MarketOutcome.NO),
        (Venue.POLYMARKET_US, "YES", MarketOutcome.YES),
        (Venue.POLYMARKET_US, "no", MarketOutcome.NO),
    ],
)
def test_venue_normalizers_produce_yes_and_no_observations(
    venue: Venue, raw_outcome: str, expected: MarketOutcome
):
    if venue is Venue.KALSHI:
        observation = _kalshi_observation(result=raw_outcome)
    else:
        observation = _polymarket_observation(
            payload=_polymarket_payload(raw_outcome)
        )

    assert observation.outcome is expected
    assert observation.market_ref.venue is venue
    assert len(observation.payload_sha256) == 64
    assert observation.authoritative_outcome_json == canonical_payload_json(
        raw_outcome
    )


@pytest.mark.parametrize("venue", [Venue.KALSHI, Venue.POLYMARKET_US])
def test_void_requires_and_preserves_explicit_refund_contract(venue: Venue):
    refund = _refund()
    if venue is Venue.KALSHI:
        observation = _kalshi_observation(result="void", void_refund=refund)
    else:
        observation = _polymarket_observation(
            payload=_polymarket_payload("VOID"), void_refund=refund
        )

    assert observation.outcome is MarketOutcome.VOID
    assert observation.void_refund == refund


@pytest.mark.parametrize("venue", [Venue.KALSHI, Venue.POLYMARKET_US])
def test_void_without_refund_contract_fails_closed(venue: Venue):
    with pytest.raises(UnsupportedVoidError):
        if venue is Venue.KALSHI:
            _kalshi_observation(result="void")
        else:
            _polymarket_observation(payload=_polymarket_payload("VOID"))


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "pm-123", "slug": "will-example-happen", "settlement": 0.5},
        {
            "id": "pm-123",
            "slug": "will-example-happen",
            "settled": True,
            "resolvedOutcome": "MAYBE",
        },
        {
            "id": "pm-123",
            "slug": "will-example-happen",
            "settled": False,
            "resolvedOutcome": "YES",
        },
    ],
)
def test_polymarket_malformed_or_nonterminal_outcomes_fail_closed(payload):
    with pytest.raises(SettlementDriftError):
        _polymarket_observation(payload=payload)


def test_kalshi_malformed_outcome_fails_closed():
    with pytest.raises(SettlementDriftError, match="outcome"):
        _kalshi_observation(result="maybe")


def test_kalshi_nonterminal_market_fails_closed():
    market = _kalshi_market(result="yes")
    market.status = "closed"

    with pytest.raises(SettlementDriftError, match="nonterminal"):
        normalize_kalshi_settlement(
            MarketRef(Venue.KALSHI, "KXTEST-26JUL", "KXTEST-26JUL"),
            market,
            observed_at=OBSERVED_AT,
            effective_at=EFFECTIVE_AT,
            rules_version=RULES_VERSION,
            source_id=SOURCE_ID,
        )


def test_venue_normalizers_reject_payload_identity_mismatch():
    kalshi_ref = MarketRef(Venue.KALSHI, "KXEXPECTED", "KXEXPECTED")
    with pytest.raises(SettlementDriftError, match="identity"):
        normalize_kalshi_settlement(
            kalshi_ref,
            _kalshi_market(ticker="KXOTHER"),
            observed_at=OBSERVED_AT,
            effective_at=EFFECTIVE_AT,
            rules_version=RULES_VERSION,
            source_id=SOURCE_ID,
        )

    payload = _polymarket_payload()
    payload["id"] = "pm-other"
    with pytest.raises(SettlementDriftError, match="identity"):
        _polymarket_observation(payload=payload)

    conflicting_payload = _polymarket_payload()
    conflicting_payload["market_id"] = "pm-other"
    with pytest.raises(SettlementDriftError, match="identity"):
        _polymarket_observation(payload=conflicting_payload)


@pytest.mark.parametrize("timestamp_field", ["observed_at", "effective_at"])
def test_observation_rejects_naive_timestamps(timestamp_field: str):
    kwargs = {
        "observed_at": OBSERVED_AT,
        "effective_at": EFFECTIVE_AT,
        timestamp_field: datetime(2026, 7, 14, 18, 0),
    }
    with pytest.raises(SettlementValidationError, match=timestamp_field):
        normalize_polymarket_settlement(
            MarketRef(Venue.POLYMARKET_US, "pm-123", "will-example-happen"),
            _polymarket_payload(),
            rules_version=RULES_VERSION,
            source_id=SOURCE_ID,
            **kwargs,
        )


@pytest.mark.parametrize(
    ("rules_version", "source_id", "match"),
    [("", SOURCE_ID, "rules_version"), (RULES_VERSION, " ", "source_id")],
)
def test_observation_rejects_missing_provenance(
    rules_version: str, source_id: str, match: str
):
    with pytest.raises(SettlementValidationError, match=match):
        normalize_polymarket_settlement(
            MarketRef(Venue.POLYMARKET_US, "pm-123", "will-example-happen"),
            _polymarket_payload(),
            observed_at=OBSERVED_AT,
            effective_at=EFFECTIVE_AT,
            rules_version=rules_version,
            source_id=source_id,
        )


def test_observation_rejects_empty_alias_identity():
    with pytest.raises(SettlementValidationError, match="alias"):
        normalize_polymarket_settlement(
            MarketRef(Venue.POLYMARKET_US, "pm-123", ""),
            {"id": "pm-123", "settled": True, "resolvedOutcome": "YES"},
            observed_at=OBSERVED_AT,
            effective_at=EFFECTIVE_AT,
            rules_version=RULES_VERSION,
            source_id=SOURCE_ID,
        )


def test_canonical_payload_hash_is_deterministic_and_payload_sensitive():
    first = _polymarket_observation()
    reordered = _polymarket_observation(
        payload={
            "resolvedOutcome": "YES",
            "settled": True,
            "slug": "will-example-happen",
            "id": "pm-123",
        }
    )
    changed = _polymarket_payload()
    changed["metadata"] = {"revision": 2}
    changed_observation = _polymarket_observation(payload=changed)

    assert first.canonical_payload_json == reordered.canonical_payload_json
    assert first.payload_sha256 == reordered.payload_sha256
    assert changed_observation.payload_sha256 != first.payload_sha256


def test_canonical_payload_rejects_unsupported_or_nonfinite_values():
    with pytest.raises(SettlementDriftError, match="unsupported"):
        canonical_payload_json({"bad": object()})
    with pytest.raises(SettlementDriftError, match="finite"):
        canonical_payload_json({"bad": float("nan")})


@pytest.mark.parametrize("venue", [Venue.KALSHI, Venue.POLYMARKET_US])
def test_changed_payload_requires_valid_supersession_for_each_venue(venue: Venue):
    if venue is Venue.KALSHI:
        previous = _kalshi_observation(result="yes")
        changed_call = lambda **kwargs: _kalshi_observation(
            result="no", previous_observation=previous, **kwargs
        )
    else:
        previous = _polymarket_observation(payload=_polymarket_payload("YES"))
        changed_call = lambda **kwargs: _polymarket_observation(
            payload=_polymarket_payload("NO"),
            previous_observation=previous,
            **kwargs,
        )

    with pytest.raises(SettlementDriftError, match="supersession"):
        changed_call()
    with pytest.raises(SettlementDriftError, match="supersession"):
        changed_call(supersedes_payload_sha256="b" * 64)

    corrected = changed_call(
        supersedes_payload_sha256=previous.payload_sha256
    )
    assert corrected.supersedes_payload_sha256 == previous.payload_sha256
    assert corrected.payload_sha256 != previous.payload_sha256


def test_settlement_contract_validates_hash_and_outcome_invariants():
    observation = _polymarket_observation()
    with pytest.raises(SettlementValidationError, match="payload_sha256"):
        replace(observation, payload_sha256="not-a-sha")
    with pytest.raises(SettlementValidationError, match="void_refund"):
        replace(observation, void_refund=_refund())


def test_settlement_contracts_are_frozen_and_refund_is_exact():
    observation = _polymarket_observation()
    refund = _refund()
    assert refund.refund_cents_per_contract == Decimal("50")

    with pytest.raises(FrozenInstanceError):
        observation.source_id = "changed"
    with pytest.raises(FrozenInstanceError):
        refund.refund_cents_per_contract = Decimal("0")


@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("100.01")])
def test_void_refund_rejects_out_of_contract_range(value: Decimal):
    with pytest.raises(SettlementValidationError, match="refund_cents_per_contract"):
        VoidRefundContract(value, refunds_entry_fee=False)
