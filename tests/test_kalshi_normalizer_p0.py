"""
P-1 RED tests for `kalshi/normalizer.py` — does NOT yet exist.

These tests lock in the post-P0 parser contract per the canonical roadmap
§7 (P0-REST-001..006 + P0-REG-021) and §11 locked design decisions LD-1,
LD-2, LD-3, LD-4, LD-16. They are expected to fail today because:

  * `kalshi.normalizer` module does not exist (ImportError at collection),
  * `KalshiMarket` does not yet carry the post-P0 fields,
  * `ExchangeState` / `UnsupportedPayloadContractError` do not yet exist.

Do NOT make these tests pass by stubbing the module. The implementation
must come in a later packet (P-2..P-5).
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

import pytest

# Module-under-test — RED on import until P-2 ships kalshi/normalizer.py.
from kalshi.normalizer import (  # type: ignore[import-not-found]  # noqa: E402
    ExchangeState,
    UnsupportedPayloadContractError,
    normalize_exchange_status,
    normalize_market_detail,
    normalize_market_list_entry,
)


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kalshi_payloads"

LIST_FIXTURE = FIXTURE_DIR / "list_markets_status_open_limit50_2026-05-11.json"
DETAIL_FIXTURE = FIXTURE_DIR / "single_market_KXTRUMPIRAN-27JAN01_2026-05-11.json"
EXCHANGE_FIXTURE = FIXTURE_DIR / "exchange_status_2026-05-11.json"
EVENT_FIXTURE = FIXTURE_DIR / "event_KXTRUMPIRAN_2026-05-11.json"
SERIES_FIXTURE = FIXTURE_DIR / "series_KXTRUMPIRAN_2026-05-11.json"
METADATA_FIXTURE = FIXTURE_DIR / "CAPTURE_METADATA_2026-05-11.json"


# Sha256 hashes — operator-locked P0-gate evidence per CLAUDE.md LD-15 / §11.5.
# CAPTURE_METADATA hash is recomputed at runtime (it carries volatile
# `capture_session` text and can be rebuilt); the five primary fixtures are
# the load-bearing audit anchors.
FIXTURE_SHA256: dict[str, str] = {
    EXCHANGE_FIXTURE.name:
        "457ba5176d7f54a556573f117659bab7f8990f8af12548a8b3cd27a12c06f383",
    LIST_FIXTURE.name:
        "6173c20ddc53380d2baccd79775cf5a5f3b80cbf264a1571935a1c49810b62f0",
    DETAIL_FIXTURE.name:
        "42375c772066b5e24ada7cd02156073bb6fa27de84ffb53d6042af93d8c7a8fc",
    EVENT_FIXTURE.name:
        "d5325604cfc58e7616fa7f1e6dfa14bc26981e4f78d6030d3cbe1701667db66d",
    SERIES_FIXTURE.name:
        "8dbebaff09308504a64e1e402309db829cd336b21410b7064cbbc895062b25a1",
}


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _expected_cents(dollars_str: str) -> int:
    """Banker's rounding (ROUND_HALF_EVEN) of dollars*100 — LD-3."""
    quantized = (Decimal(dollars_str) * Decimal(100)).quantize(
        Decimal("1"), rounding=ROUND_HALF_EVEN
    )
    return int(quantized)


# ---------------------------------------------------------------------------
# P0-REST-001 — list payload, dollars → cents
# ---------------------------------------------------------------------------

def test_p0_rest_001_list_payload_dollars_parses_cents_consistent():
    """Every list entry with *_dollars fields parses to integer cents.

    Locks: LD-3 (ROUND_HALF_EVEN, Decimal arithmetic) and LD-16
    (`price_method="dollars_fixed_point"` provenance).
    """
    payload = _load_json(LIST_FIXTURE)
    assert "markets" in payload, "list payload missing 'markets' key"
    parsed_count = 0
    for market_payload in payload["markets"]:
        # Some entries may be missing pricing entirely (provisional markets).
        # Those are P0-REST-003 territory; this test asserts on the entries
        # that DO carry *_dollars fields.
        if "yes_bid_dollars" not in market_payload:
            continue
        m = normalize_market_list_entry(market_payload)
        parsed_count += 1
        assert m.price_available is True, (
            f"market {market_payload.get('ticker')}: price_available must "
            "be True when *_dollars present"
        )
        assert m.price_method == "dollars_fixed_point", (
            f"market {market_payload.get('ticker')}: price_method must be "
            f"'dollars_fixed_point', got {m.price_method!r}"
        )
        assert isinstance(m.yes_bid_cents, int)
        assert isinstance(m.yes_ask_cents, int)
        assert isinstance(m.no_bid_cents, int)
        assert isinstance(m.no_ask_cents, int)
        assert m.yes_bid_cents == _expected_cents(market_payload["yes_bid_dollars"])
        assert m.yes_ask_cents == _expected_cents(market_payload["yes_ask_dollars"])
        assert m.no_bid_cents == _expected_cents(market_payload["no_bid_dollars"])
        assert m.no_ask_cents == _expected_cents(market_payload["no_ask_dollars"])

        # No latent 50 cent fallback. ROUND_HALF_EVEN at the parse boundary
        # legitimately rounds sub-cent source values to 50¢ (e.g. 0.5020,
        # 0.4980, 0.5050, 0.4950 → 50); the regression we guard against is
        # the silent 50 from a missing/null source field.
        for field_name, dollars_key in (
            ("yes_bid_cents", "yes_bid_dollars"),
            ("yes_ask_cents", "yes_ask_dollars"),
            ("no_bid_cents",  "no_bid_dollars"),
            ("no_ask_cents",  "no_ask_dollars"),
        ):
            cents = getattr(m, field_name)
            if cents != 50:
                continue
            src_raw = market_payload.get(dollars_key)
            assert src_raw is not None and src_raw != "", (
                f"market {m.ticker}: {field_name}==50 but source "
                f"{dollars_key}={src_raw!r} is missing/null"
            )
            rounded = (Decimal(str(src_raw)) * Decimal("100")).quantize(
                Decimal("1"), rounding=ROUND_HALF_EVEN
            )
            assert rounded == Decimal("50"), (
                f"market {m.ticker}: {field_name}==50 but source "
                f"{dollars_key}={src_raw!r} does not ROUND_HALF_EVEN to 50"
            )

    assert parsed_count > 0, "No markets in list fixture exercised parser"


# ---------------------------------------------------------------------------
# P0-REST-002 — legacy cents-int payload (no *_dollars)
# ---------------------------------------------------------------------------

def test_p0_rest_002_legacy_cents_int_payload_parses_without_50_fallback():
    """Synthetic legacy payload — proves parser handles old cent-ints
    without silently falling back to 50."""
    payload = {
        "ticker": "X-LEGACY",
        "title": "Legacy synthetic",
        "yes_bid": 38,
        "yes_ask": 40,
        "no_bid": 60,
        "no_ask": 62,
        "status": "active",
        "close_time": "2027-01-01T00:00:00Z",
    }
    m = normalize_market_list_entry(payload)
    assert m.price_available is True
    assert m.yes_bid_cents == 38
    assert m.yes_ask_cents == 40
    assert m.no_bid_cents == 60
    assert m.no_ask_cents == 62
    assert m.price_method == "legacy_cents", (
        f"legacy payload should produce price_method='legacy_cents', "
        f"got {m.price_method!r}"
    )
    # No defaulted 50s anywhere.
    for f in ("yes_bid_cents", "yes_ask_cents", "no_bid_cents", "no_ask_cents"):
        assert getattr(m, f) != 50, f"{f} fell back to 50¢ silently"


# ---------------------------------------------------------------------------
# P0-REST-003 — all price fields absent → price_available=False
# ---------------------------------------------------------------------------

def test_p0_rest_003_all_price_fields_absent_marks_unusable():
    """Market with no price fields at all is marked unavailable, not 50."""
    payload = {
        "ticker": "X-NOPRICE",
        "title": "No price",
        "status": "active",
        "close_time": "2027-01-01T00:00:00Z",
    }
    m = normalize_market_list_entry(payload)
    assert m.price_available is False
    assert m.yes_bid_cents is None
    assert m.yes_ask_cents is None
    assert m.no_bid_cents is None
    assert m.no_ask_cents is None
    assert m.price_method == "none"


# ---------------------------------------------------------------------------
# P0-REST-004 — malformed price values fail closed, never 50
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_value",
    [None, "", "abc", "not-a-number"],
    ids=["none", "empty", "alpha", "garbage"],
)
def test_p0_rest_004_malformed_prices_fail_closed(bad_value):
    """Parametrized: a single malformed price field forces fail-closed."""
    payload = {
        "ticker": "X-BADPRICE",
        "title": "Bad price",
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": bad_value,
        "no_bid_dollars": "0.60",
        "no_ask_dollars": "0.62",
        "status": "active",
        "close_time": "2027-01-01T00:00:00Z",
    }
    try:
        m = normalize_market_list_entry(payload)
    except UnsupportedPayloadContractError:
        return  # acceptable fail-closed mode
    # Alternative acceptable outcome: parsed but price_available=False.
    assert m.price_available is False, (
        f"malformed value {bad_value!r} must EITHER raise "
        f"UnsupportedPayloadContractError OR set price_available=False; "
        f"got price_available={m.price_available}"
    )
    for f in ("yes_bid_cents", "yes_ask_cents", "no_bid_cents", "no_ask_cents"):
        assert getattr(m, f) != 50, f"{f} defaulted to 50 on malformed input"


# ---------------------------------------------------------------------------
# P0-REST-005 — detail endpoint payload (wrapped {"market": {...}})
# ---------------------------------------------------------------------------

def test_p0_rest_005_detail_endpoint_parses_cents_consistent():
    """Detail endpoint for KXTRUMPIRAN-27JAN01.

    Source values (dollars):
      yes_bid=0.0880 → 9c (8.80 banker-rounds to 9)
      yes_ask=0.0980 → 10c
      no_bid=0.9020 → 90c
      no_ask=0.9120 → 91c
    """
    payload = _load_json(DETAIL_FIXTURE)
    m = normalize_market_detail(payload)
    assert m.ticker == "KXTRUMPIRAN-27JAN01"
    assert m.price_available is True
    assert m.price_method == "dollars_fixed_point"
    assert m.yes_bid_cents == 9
    assert m.yes_ask_cents == 10
    assert m.no_bid_cents == 90
    assert m.no_ask_cents == 91
    assert m.last_price_cents == 9     # 0.0880 → 9
    assert m.previous_price_cents == 8  # 0.0760 → 8 (7.60 banker-rounds to 8)
    assert m.status == "active"
    assert m.price_level_structure == "tapered_deci_cent"
    assert m.fractional_trading_enabled is True
    assert m.market_type == "binary"
    # Provenance per LD-16: raw payload hash must be the sha256 of sorted-keys
    # utf-8 serialization of the inner market dict (not the wrapping envelope).
    assert m.raw_payload_hash is not None
    assert len(m.raw_payload_hash) == 64
    int(m.raw_payload_hash, 16)  # valid hex


def test_market_detail_preserves_rules_and_source_metadata_shadow_only():
    """Detail normalization preserves public resolution/source text as optional metadata.

    This is shadow-only metadata for downstream hint generation; preserving it
    must not alter the existing normalized market fields or price availability.
    """
    payload = {
        "market": {
            "ticker": "KXSOURCEHINT-TEST",
            "title": "Will an official source confirm the event?",
            "subtitle": "Before Jan 1, 2027",
            "yes_bid_dollars": "0.4100",
            "yes_ask_dollars": "0.4300",
            "no_bid_dollars": "0.5700",
            "no_ask_dollars": "0.5900",
            "status": "active",
            "close_time": "2027-01-01T00:00:00Z",
            "rules_primary": (
                "If the event is confirmed before Jan 1, 2027, then the "
                "market resolves to Yes."
            ),
            "rules_secondary": (
                "Evidence must be reported by at least one Source Agency "
                "and may include official announcements or news reports."
            ),
            "resolution_source": (
                "The market will use public reporting from Reuters, AP, "
                "The Guardian, or official government agencies."
            ),
        }
    }

    m = normalize_market_detail(payload)

    assert m.ticker == "KXSOURCEHINT-TEST"
    assert m.title == "Will an official source confirm the event?"
    assert m.subtitle == "Before Jan 1, 2027"
    assert m.price_available is True
    assert m.yes_bid_cents == 41
    assert m.yes_ask_cents == 43
    assert m.no_bid_cents == 57
    assert m.no_ask_cents == 59
    assert m.market_metadata == {
        "rules_primary": payload["market"]["rules_primary"],
        "rules_secondary": payload["market"]["rules_secondary"],
        "resolution_source": payload["market"]["resolution_source"],
    }


def test_market_metadata_defaults_to_empty_isolated_dict():
    market_a = normalize_market_list_entry({
        "ticker": "KXMETA-A",
        "title": "No metadata A",
        "status": "active",
        "close_time": "2027-01-01T00:00:00Z",
    })
    market_b = normalize_market_list_entry({
        "ticker": "KXMETA-B",
        "title": "No metadata B",
        "status": "active",
        "close_time": "2027-01-01T00:00:00Z",
    })

    assert market_a.market_metadata == {}
    assert market_b.market_metadata == {}
    assert market_a.market_metadata is not market_b.market_metadata


# ---------------------------------------------------------------------------
# P0-REST-006 — inverted spread rejected
# ---------------------------------------------------------------------------

def test_p0_rest_006_inverted_spread_rejected():
    """yes_bid > yes_ask is structurally invalid — D3 invariant."""
    payload = {
        "ticker": "X-INVERTED",
        "title": "Inverted spread",
        "yes_bid_dollars": "0.60",
        "yes_ask_dollars": "0.40",  # bid > ask
        "no_bid_dollars": "0.40",
        "no_ask_dollars": "0.60",
        "status": "active",
        "close_time": "2027-01-01T00:00:00Z",
    }
    try:
        m = normalize_market_list_entry(payload)
    except UnsupportedPayloadContractError:
        return
    assert m.price_available is False, (
        "inverted spread must either raise UnsupportedPayloadContractError "
        f"or set price_available=False; got price_available={m.price_available}"
    )


def test_p0_rest_006b_complement_violation_rejected():
    """yes_bid + no_ask > 100 violates binary complement — D3 invariant."""
    payload = {
        "ticker": "X-COMPLEMENT",
        "title": "Complement violation",
        "yes_bid_dollars": "0.60",
        "yes_ask_dollars": "0.62",
        "no_bid_dollars": "0.50",
        "no_ask_dollars": "0.55",  # 60 + 55 = 115 > 100
        "status": "active",
        "close_time": "2027-01-01T00:00:00Z",
    }
    try:
        m = normalize_market_list_entry(payload)
    except UnsupportedPayloadContractError:
        return
    assert m.price_available is False


# ---------------------------------------------------------------------------
# P0-REG-021 — fixture pinning by sha256 (mutation detector)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture_name,expected_sha256",
    list(FIXTURE_SHA256.items()),
)
def test_p0_fixture_pinning_sha256(fixture_name: str, expected_sha256: str):
    """If a fixture is mutated, this test fails immediately. The fixtures
    are P0-gate evidence per CLAUDE.md LD-15 and must not change without
    re-capture."""
    path = FIXTURE_DIR / fixture_name
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    assert actual == expected_sha256, (
        f"Fixture {fixture_name} sha256 drift: expected {expected_sha256}, "
        f"got {actual}. Captured fixtures must not be edited."
    )


# ---------------------------------------------------------------------------
# P0-REG-021 — no latent 50 constants in parsed fixtures
# ---------------------------------------------------------------------------

def test_p0_reg_021_no_latent_50_constants_in_fixtures():
    """Catches silent-50 fallback bug: any parsed ``*_cents == 50`` must
    trace back to a source-dollars value that legitimately rounds to 50
    via ROUND_HALF_EVEN. A ``cents==50`` from missing/null source = bug.

    LD-16 (ROUND_HALF_EVEN at the parse boundary) legitimately produces
    ``cents==50`` from a band of sub-cent source values around ``0.50``
    (e.g. ``0.5020 → 50``, ``0.4980 → 50``, ``0.5050 → 50`` and
    ``0.4950 → 50`` under banker's rounding). The regression we guard
    against is the *latent* 50 — a midpoint that defaults to 50 when the
    source field is missing/null.
    """
    payload = _load_json(LIST_FIXTURE)
    parsed_count = 0
    for market_payload in payload["markets"]:
        if "yes_bid_dollars" not in market_payload:
            continue
        m = normalize_market_list_entry(market_payload)
        parsed_count += 1
        for field_name, dollars_key in (
            ("yes_bid_cents", "yes_bid_dollars"),
            ("yes_ask_cents", "yes_ask_dollars"),
            ("no_bid_cents",  "no_bid_dollars"),
            ("no_ask_cents",  "no_ask_dollars"),
        ):
            cents = getattr(m, field_name)
            if cents != 50:
                continue
            src_raw = market_payload.get(dollars_key)
            # Missing/null source → cents==50 is the silent-50 fallback bug.
            assert src_raw is not None and src_raw != "", (
                f"{m.ticker}.{field_name}==50 but source "
                f"{dollars_key}={src_raw!r} is missing/null — "
                "latent 50 fallback re-introduced"
            )
            rounded = (Decimal(str(src_raw)) * Decimal("100")).quantize(
                Decimal("1"), rounding=ROUND_HALF_EVEN
            )
            assert rounded == Decimal("50"), (
                f"{m.ticker}.{field_name}==50 but source "
                f"{dollars_key}={src_raw!r} does not ROUND_HALF_EVEN to "
                f"50 (got {rounded}) — latent 50 fallback re-introduced"
            )
    # Witness assertion: if iteration produced zero parsed markets (empty
    # fixture, loader bug returns nothing, filter eats every row) the test
    # passes vacuously without the invariant being exercised. Fail loudly.
    assert parsed_count > 0, (
        "test must iterate at least one market or it passes vacuously"
    )


# ---------------------------------------------------------------------------
# Property-based parser round-trip — LD-3
# ---------------------------------------------------------------------------

def test_p0_parser_round_trip_property_based():
    """Hypothesis fuzz: yes_ask dollars → cents → assert ROUND_HALF_EVEN."""
    from hypothesis import assume, given, settings
    from hypothesis import strategies as st

    @given(
        yes_bid_d=st.decimals(
            min_value=Decimal("0.00"), max_value=Decimal("1.00"),
            places=2, allow_nan=False, allow_infinity=False,
        ),
        yes_ask_d=st.decimals(
            min_value=Decimal("0.00"), max_value=Decimal("1.00"),
            places=2, allow_nan=False, allow_infinity=False,
        ),
    )
    @settings(max_examples=80, deadline=None)
    def _prop(yes_bid_d: Decimal, yes_ask_d: Decimal):
        # Skip parser-rejection (inverted-spread or complement) cases.
        assume(yes_bid_d <= yes_ask_d)
        no_bid_d = (Decimal("1.00") - yes_ask_d).quantize(Decimal("0.01"))
        no_ask_d = (Decimal("1.00") - yes_bid_d).quantize(Decimal("0.01"))
        assume(yes_bid_d + no_ask_d <= Decimal("1.00"))
        payload = {
            "ticker": "X-PROP",
            "title": "Property fuzz",
            "yes_bid_dollars": f"{yes_bid_d:.4f}",
            "yes_ask_dollars": f"{yes_ask_d:.4f}",
            "no_bid_dollars":  f"{no_bid_d:.4f}",
            "no_ask_dollars":  f"{no_ask_d:.4f}",
            "status": "active",
            "close_time": "2027-01-01T00:00:00Z",
        }
        m = normalize_market_list_entry(payload)
        assert m.price_available is True
        assert m.yes_bid_cents == _expected_cents(payload["yes_bid_dollars"])
        assert m.yes_ask_cents == _expected_cents(payload["yes_ask_dollars"])
        assert m.no_bid_cents == _expected_cents(payload["no_bid_dollars"])
        assert m.no_ask_cents == _expected_cents(payload["no_ask_dollars"])
        # cents are integers in [0, 100]
        assert 0 <= m.yes_bid_cents <= 100
        assert 0 <= m.yes_ask_cents <= 100

    _prop()


# ---------------------------------------------------------------------------
# Exchange status normalization
# ---------------------------------------------------------------------------

def test_p0_normalize_exchange_status_parses():
    """Live exchange_status fixture parses to (active, active, is_open=True)."""
    payload = _load_json(EXCHANGE_FIXTURE)
    state = normalize_exchange_status(payload)
    assert isinstance(state, ExchangeState)
    assert state.exchange_active is True
    assert state.trading_active is True
    assert state.is_open is True
