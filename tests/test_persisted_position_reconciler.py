from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from polymarket.settlement_reconciler import (
    PersistedPositionReconciler,
    SettlementNotFound,
    VenueRoutingAuthoritativeSettlementSource,
)
from trading.settlement import (
    MarketOutcome,
    SettlementDriftError,
    SettlementObservation,
    build_settlement_observation,
)
from trading.venue import MarketRef, Venue


_NOW = datetime(2026, 7, 14, 20, 0, tzinfo=timezone.utc)


def _observation(market_ref: MarketRef) -> SettlementObservation:
    return build_settlement_observation(
        market_ref=market_ref,
        outcome=MarketOutcome.YES,
        authoritative_outcome="yes",
        authoritative_payload={
            "market_id": market_ref.venue_market_id,
            "venue": market_ref.venue.value,
        },
        observed_at=_NOW,
        effective_at=_NOW,
        rules_version="test-v1",
        source_id="test-authority",
    )


class _Source:
    def __init__(self, results: dict[MarketRef, object]) -> None:
        self.results = results
        self.calls: list[MarketRef] = []

    def get_settlement(self, market_ref: MarketRef) -> SettlementObservation | None:
        self.calls.append(market_ref)
        result = self.results[market_ref]
        if isinstance(result, BaseException):
            raise result
        return result if isinstance(result, SettlementObservation) else None


class _Resolver:
    def __init__(self, market_refs: tuple[MarketRef, ...]) -> None:
        self.market_refs = market_refs
        self.resolved: list[SettlementObservation] = []

    def mapped_open_market_refs(self) -> tuple[MarketRef, ...]:
        return self.market_refs

    def resolve_observation(self, observation: SettlementObservation) -> bool:
        self.resolved.append(observation)
        return True

    def _resolve_market_sync(self, *_args, **_kwargs):
        raise AssertionError("canonical reconciliation must not use legacy booleans")


def test_reconciler_routes_same_alias_by_exact_two_venue_market_ref():
    kalshi = MarketRef(Venue.KALSHI, "KXGDP-26JUL31", "shared-alias")
    polymarket = MarketRef(Venue.POLYMARKET_US, "104982", "shared-alias")
    source = _Source({kalshi: _observation(kalshi), polymarket: _observation(polymarket)})
    resolver = _Resolver((kalshi, polymarket))

    result = PersistedPositionReconciler(source=source, resolver=resolver).reconcile()

    assert source.calls == [kalshi, polymarket]
    assert [item.market_ref for item in resolver.resolved] == [kalshi, polymarket]
    assert (result.checked, result.resolved, result.not_found, result.errors) == (2, 2, 0, 0)


def test_reconciler_deduplicates_multiple_trades_for_same_market_ref():
    market_ref = MarketRef(Venue.KALSHI, "KXGDP-26JUL31", "gdp")
    source = _Source({market_ref: _observation(market_ref)})
    resolver = _Resolver((market_ref, market_ref))

    result = PersistedPositionReconciler(source=source, resolver=resolver).reconcile()

    assert source.calls == [market_ref]
    assert len(resolver.resolved) == 1
    assert result.checked == result.resolved == 1


def test_reconciler_isolates_not_found_and_continues():
    missing = MarketRef(Venue.KALSHI, "KXMISSING", "missing")
    settled = MarketRef(Venue.POLYMARKET_US, "104982", "settled")
    source = _Source(
        {
            missing: SettlementNotFound("not settled"),
            settled: _observation(settled),
        }
    )
    resolver = _Resolver((missing, settled))

    result = PersistedPositionReconciler(source=source, resolver=resolver).reconcile()

    assert source.calls == [missing, settled]
    assert [item.market_ref for item in resolver.resolved] == [settled]
    assert (result.checked, result.resolved, result.not_found, result.errors) == (2, 1, 1, 0)


def test_reconciler_isolates_unexpected_fetch_error_and_continues():
    broken = MarketRef(Venue.KALSHI, "KXBROKEN", "broken")
    settled = MarketRef(Venue.POLYMARKET_US, "104982", "settled")
    source = _Source(
        {
            broken: OSError("temporary authority outage"),
            settled: _observation(settled),
        }
    )
    resolver = _Resolver((broken, settled))

    result = PersistedPositionReconciler(source=source, resolver=resolver).reconcile()

    assert source.calls == [broken, settled]
    assert [item.market_ref for item in resolver.resolved] == [settled]
    assert (result.checked, result.resolved, result.not_found, result.errors) == (2, 1, 0, 1)


def test_reconciler_rejects_observation_identity_drift_before_mutation():
    expected = MarketRef(Venue.KALSHI, "KXEXPECTED", "shared")
    wrong = MarketRef(Venue.KALSHI, "KXWRONG", "shared")
    source = _Source({expected: _observation(wrong)})
    resolver = _Resolver((expected,))

    with pytest.raises(SettlementDriftError, match="identity"):
        PersistedPositionReconciler(source=source, resolver=resolver).reconcile()

    assert resolver.resolved == []


def test_authoritative_source_fetches_and_normalizes_kalshi_by_canonical_id():
    market_ref = MarketRef(Venue.KALSHI, "KXGDP-26JUL31", "KXGDP-26JUL31")
    kalshi_source = SimpleNamespace(
        get_market=lambda market_id: SimpleNamespace(
            ticker=market_id,
            status="settled",
            result="yes",
            expiration_time="2026-07-31T16:00:00Z",
            raw_payload_hash="a" * 64,
            updated_time=_NOW,
        )
    )
    polymarket_source = SimpleNamespace(
        get_settlement=lambda _market_id: pytest.fail("wrong venue route")
    )
    source = VenueRoutingAuthoritativeSettlementSource(
        kalshi_source=kalshi_source,
        polymarket_source=polymarket_source,
        clock=lambda: _NOW,
    )

    observation = source.get_settlement(market_ref)

    assert observation is not None
    assert observation.market_ref == market_ref
    assert observation.outcome is MarketOutcome.YES
    assert observation.observed_at == _NOW
    assert observation.effective_at == _NOW


def test_authoritative_source_treats_nonterminal_kalshi_as_not_found():
    market_ref = MarketRef(Venue.KALSHI, "KXGDP-26JUL31", "KXGDP-26JUL31")
    source = VenueRoutingAuthoritativeSettlementSource(
        kalshi_source=SimpleNamespace(
            get_market=lambda market_id: SimpleNamespace(
                ticker=market_id,
                status="open",
                result="",
                expiration_time="2026-07-31T16:00:00Z",
                raw_payload_hash="a" * 64,
                updated_time=_NOW,
            )
        ),
        polymarket_source=SimpleNamespace(),
        clock=lambda: _NOW,
    )

    assert source.get_settlement(market_ref) is None


def test_authoritative_source_fetches_and_normalizes_polymarket_by_canonical_id():
    market_ref = MarketRef(Venue.POLYMARKET_US, "104982", "gdp-q2")
    requested_ids: list[str] = []

    def get_settlement(market_id: str):
        requested_ids.append(market_id)
        return {"id": market_id, "settled": True, "settlement": 1}

    source = VenueRoutingAuthoritativeSettlementSource(
        kalshi_source=SimpleNamespace(
            get_market=lambda _market_id: pytest.fail("wrong venue route")
        ),
        polymarket_source=SimpleNamespace(get_settlement=get_settlement),
        clock=lambda: _NOW,
    )

    observation = source.get_settlement(market_ref)

    assert requested_ids == ["104982"]
    assert observation is not None
    assert observation.market_ref == market_ref
    assert observation.outcome is MarketOutcome.YES
