from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
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
    observed_at: datetime = OBSERVED_AT,
    effective_at: datetime = EFFECTIVE_AT,
    rules_version: str = RULES_VERSION,
    source_id: str = SOURCE_ID,
    previous_observation: SettlementObservation | None = None,
    supersedes_observation_sha256: str | None = None,
    void_refund: VoidRefundContract | None = None,
) -> SettlementObservation:
    return normalize_kalshi_settlement(
        MarketRef(Venue.KALSHI, "KXTEST-26JUL", "KXTEST-26JUL"),
        _kalshi_market(result=result),
        observed_at=observed_at,
        effective_at=effective_at,
        rules_version=rules_version,
        source_id=source_id,
        void_refund=void_refund,
        previous_observation=previous_observation,
        supersedes_observation_sha256=supersedes_observation_sha256,
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
    observed_at: datetime = OBSERVED_AT,
    effective_at: datetime = EFFECTIVE_AT,
    rules_version: str = RULES_VERSION,
    source_id: str = SOURCE_ID,
    previous_observation: SettlementObservation | None = None,
    supersedes_observation_sha256: str | None = None,
    void_refund: VoidRefundContract | None = None,
) -> SettlementObservation:
    return normalize_polymarket_settlement(
        MarketRef(Venue.POLYMARKET_US, "pm-123", "will-example-happen"),
        payload or _polymarket_payload(),
        observed_at=observed_at,
        effective_at=effective_at,
        rules_version=rules_version,
        source_id=source_id,
        void_refund=void_refund,
        previous_observation=previous_observation,
        supersedes_observation_sha256=supersedes_observation_sha256,
    )


def _live_polymarket_observation(
    payload: dict[str, object],
    *,
    market_id: str = "8594",
    alias: str = "aqc-cbb-f4-2026-04-06-kan",
) -> SettlementObservation:
    return normalize_polymarket_settlement(
        MarketRef(Venue.POLYMARKET_US, market_id, alias),
        payload,
        observed_at=OBSERVED_AT,
        effective_at=EFFECTIVE_AT,
        rules_version=RULES_VERSION,
        source_id=SOURCE_ID,
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
    assert len(observation.observation_sha256) == 64
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


def test_polymarket_live_numeric_settlement_shape_allows_missing_canonical_id():
    # The official endpoint currently returns exactly settlement + slug. The
    # authenticated client verifies that slug before this pure boundary.
    observation = _live_polymarket_observation(
        {"settlement": 1, "slug": "aqc-cbb-f4-2026-04-06-kan"}
    )

    assert observation.outcome is MarketOutcome.YES


def test_polymarket_live_shape_accepts_matching_numeric_canonical_id_fields():
    observation = _live_polymarket_observation(
        {
            "settlement": 0,
            "slug": "aqc-cbb-f4-2026-04-06-kan",
            "id": 8594,
            "market_id": "8594",
            "marketId": 8594,
        }
    )

    assert observation.outcome is MarketOutcome.NO


@pytest.mark.parametrize("field", ["id", "market_id", "marketId"])
def test_polymarket_rejects_each_mismatched_canonical_id_field(field: str):
    payload = {
        "settlement": 1,
        "slug": "aqc-cbb-f4-2026-04-06-kan",
        field: "9999",
    }

    with pytest.raises(SettlementDriftError, match="identity"):
        _live_polymarket_observation(payload)


def test_polymarket_present_slug_must_match_alias_not_canonical_id():
    payload = {"settlement": 1, "slug": "8594", "id": "8594"}

    with pytest.raises(SettlementDriftError, match="alias"):
        _live_polymarket_observation(payload)


def test_polymarket_textual_shape_requires_canonical_id_and_settled_true():
    with pytest.raises(SettlementDriftError, match="canonical identity"):
        _live_polymarket_observation(
            {
                "slug": "aqc-cbb-f4-2026-04-06-kan",
                "settled": True,
                "resolvedOutcome": "YES",
            }
        )

    observation = _live_polymarket_observation(
        {"id": "8594", "settled": True, "resolvedOutcome": "YES"}
    )
    assert observation.outcome is MarketOutcome.YES


@pytest.mark.parametrize("settled", [False, 1, "true", None])
def test_polymarket_settled_marker_must_be_exact_boolean_true(settled):
    with pytest.raises(SettlementDriftError, match="settled"):
        _live_polymarket_observation(
            {
                "id": "8594",
                "settled": settled,
                "resolvedOutcome": "YES",
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": "8594",
            "slug": "aqc-cbb-f4-2026-04-06-kan",
            "resolvedOutcome": "YES",
        },
        {"settlement": "1", "slug": "aqc-cbb-f4-2026-04-06-kan"},
        {
            "settlement": 1,
            "slug": "aqc-cbb-f4-2026-04-06-kan",
            "metadata": "unexpected",
        },
    ],
)
def test_polymarket_without_settled_marker_accepts_only_exact_live_shape(payload):
    with pytest.raises(SettlementDriftError, match="live settlement shape"):
        _live_polymarket_observation(payload)


def test_polymarket_all_authoritative_outcome_fields_must_agree():
    payload = {
        "id": "8594",
        "slug": "aqc-cbb-f4-2026-04-06-kan",
        "settled": True,
        "settlement": 1,
        "resolvedOutcome": "YES",
        "resolved_outcome": "yes",
        "outcome": "YES",
        "result": "yes",
    }
    observation = _live_polymarket_observation(payload)
    assert observation.outcome is MarketOutcome.YES

    for field in ("resolvedOutcome", "resolved_outcome", "outcome", "result"):
        conflicting = dict(payload)
        conflicting[field] = "NO"
        with pytest.raises(SettlementDriftError, match="conflicting"):
            _live_polymarket_observation(conflicting)

    conflicting = dict(payload)
    conflicting["settlement"] = 0
    with pytest.raises(SettlementDriftError, match="conflicting"):
        _live_polymarket_observation(conflicting)


def test_polymarket_rejects_malformed_authoritative_field_even_when_another_agrees():
    with pytest.raises(SettlementDriftError, match="outcome"):
        _live_polymarket_observation(
            {
                "id": "8594",
                "settled": True,
                "settlement": 1,
                "result": {"value": "YES"},
            }
        )


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


@pytest.mark.parametrize("venue", [Venue.KALSHI, Venue.POLYMARKET_US])
def test_venue_normalizers_reject_effective_time_after_observation(venue: Venue):
    kwargs = {
        "observed_at": OBSERVED_AT,
        "effective_at": OBSERVED_AT + timedelta(seconds=1),
    }

    with pytest.raises(SettlementValidationError, match="effective_at"):
        if venue is Venue.KALSHI:
            _kalshi_observation(**kwargs)
        else:
            _polymarket_observation(**kwargs)


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
    assert first.observation_sha256 == reordered.observation_sha256
    assert changed_observation.payload_sha256 != first.payload_sha256
    assert changed_observation.observation_sha256 != first.observation_sha256


def test_observation_fingerprint_normalizes_effective_time_to_utc():
    utc = _polymarket_observation()
    same_instant = _polymarket_observation(
        effective_at=EFFECTIVE_AT.astimezone(
            timezone(timedelta(hours=-6))
        )
    )

    assert same_instant.observation_sha256 == utc.observation_sha256


@pytest.mark.parametrize(
    "changed_fields",
    [
        {
            "market_ref": MarketRef(
                Venue.POLYMARKET_US,
                "pm-123",
                "different-alias",
            )
        },
        {"outcome": MarketOutcome.NO},
        {"authoritative_outcome_json": canonical_payload_json("NO")},
    ],
)
def test_observation_fingerprint_binds_identity_and_authoritative_outcome(
    changed_fields,
):
    observation = _polymarket_observation()

    with pytest.raises(SettlementValidationError, match="observation_sha256"):
        replace(observation, **changed_fields)


@pytest.mark.parametrize(
    "changed_kwargs",
    [
        {"rules_version": "official-rules-v2"},
        {"source_id": "official-api:corrected"},
        {"effective_at": EFFECTIVE_AT + timedelta(seconds=1)},
    ],
)
def test_same_payload_semantic_change_requires_observation_supersession(
    changed_kwargs,
):
    previous = _polymarket_observation()

    with pytest.raises(SettlementDriftError, match="supersession"):
        _polymarket_observation(
            previous_observation=previous,
            **changed_kwargs,
        )

    corrected = _polymarket_observation(
        previous_observation=previous,
        supersedes_observation_sha256=previous.observation_sha256,
        **changed_kwargs,
    )
    assert corrected.payload_sha256 == previous.payload_sha256
    assert corrected.observation_sha256 != previous.observation_sha256


def test_same_void_payload_changed_refund_requires_observation_supersession():
    previous = _polymarket_observation(
        payload=_polymarket_payload("VOID"),
        void_refund=_refund(),
    )
    changed_refund = VoidRefundContract(
        refund_cents_per_contract=Decimal("50.00"),
        refunds_entry_fee=True,
    )

    with pytest.raises(SettlementDriftError, match="supersession"):
        _polymarket_observation(
            payload=_polymarket_payload("VOID"),
            void_refund=changed_refund,
            previous_observation=previous,
        )

    corrected = _polymarket_observation(
        payload=_polymarket_payload("VOID"),
        void_refund=changed_refund,
        previous_observation=previous,
        supersedes_observation_sha256=previous.observation_sha256,
    )
    assert corrected.payload_sha256 == previous.payload_sha256
    assert corrected.observation_sha256 != previous.observation_sha256


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
        changed_call(supersedes_observation_sha256="b" * 64)

    corrected = changed_call(
        supersedes_observation_sha256=previous.observation_sha256
    )
    assert (
        corrected.supersedes_observation_sha256
        == previous.observation_sha256
    )
    assert corrected.payload_sha256 != previous.payload_sha256


@pytest.mark.parametrize("venue", [Venue.KALSHI, Venue.POLYMARKET_US])
def test_changed_supersession_rejects_regressing_observed_time(venue: Venue):
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

    with pytest.raises(SettlementDriftError, match="observed_at"):
        changed_call(
            observed_at=previous.observed_at - timedelta(seconds=1),
            effective_at=previous.effective_at,
            supersedes_observation_sha256=previous.observation_sha256,
        )


@pytest.mark.parametrize("venue", [Venue.KALSHI, Venue.POLYMARKET_US])
def test_changed_supersession_rejects_regressing_effective_time(venue: Venue):
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

    with pytest.raises(SettlementDriftError, match="effective_at"):
        changed_call(
            observed_at=previous.observed_at + timedelta(seconds=1),
            effective_at=previous.effective_at - timedelta(seconds=1),
            supersedes_observation_sha256=previous.observation_sha256,
        )


@pytest.mark.parametrize("venue", [Venue.KALSHI, Venue.POLYMARKET_US])
def test_changed_supersession_allows_equal_timestamps(venue: Venue):
    if venue is Venue.KALSHI:
        previous = _kalshi_observation(result="yes")
        corrected = _kalshi_observation(
            result="no",
            previous_observation=previous,
            supersedes_observation_sha256=previous.observation_sha256,
        )
    else:
        previous = _polymarket_observation(payload=_polymarket_payload("YES"))
        corrected = _polymarket_observation(
            payload=_polymarket_payload("NO"),
            previous_observation=previous,
            supersedes_observation_sha256=previous.observation_sha256,
        )

    assert corrected.observed_at == previous.observed_at
    assert corrected.effective_at == previous.effective_at


@pytest.mark.parametrize("venue", [Venue.KALSHI, Venue.POLYMARKET_US])
def test_identical_observation_may_repeat_later_but_not_earlier(venue: Venue):
    if venue is Venue.KALSHI:
        previous = _kalshi_observation()
        repeat_call = lambda **kwargs: _kalshi_observation(
            previous_observation=previous, **kwargs
        )
    else:
        previous = _polymarket_observation()
        repeat_call = lambda **kwargs: _polymarket_observation(
            previous_observation=previous, **kwargs
        )

    repeated = repeat_call(
        observed_at=previous.observed_at + timedelta(seconds=1),
        effective_at=previous.effective_at,
    )
    assert repeated.payload_sha256 == previous.payload_sha256
    assert repeated.observation_sha256 == previous.observation_sha256

    with pytest.raises(SettlementDriftError, match="observed_at"):
        repeat_call(
            observed_at=previous.observed_at - timedelta(seconds=1),
            effective_at=previous.effective_at,
        )


def test_settlement_contract_validates_hash_and_outcome_invariants():
    observation = _polymarket_observation()
    with pytest.raises(SettlementValidationError, match="payload_sha256"):
        replace(observation, payload_sha256="not-a-sha")
    with pytest.raises(SettlementValidationError, match="observation_sha256"):
        replace(observation, observation_sha256="not-a-sha")
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
