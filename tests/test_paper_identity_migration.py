from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from kalshi.normalizer import UnsupportedPayloadContractError
from kalshi.rest_client import KalshiRestClient
from polymarket.public_client import PolymarketPublicClient
from scripts.migrate_paper_market_identity import (
    IdentityLookup,
    PublicVenueIdentityResolver,
    _parse_args,
    apply_identity_plan,
    main,
    open_readonly,
    plan_identity_migration,
)
from trading.paper_trader import PaperTrader, _DDL
from trading.venue import Venue


def _create_db(path: Path, *, with_identity: bool = False) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE paper_trades (
            trade_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            venue TEXT NOT NULL DEFAULT 'kalshi',
            resolved INTEGER DEFAULT 0,
            venue_market_id TEXT,
            identity_status TEXT,
            quarantine_reason TEXT
        )
        """
        if with_identity
        else """
        CREATE TABLE paper_trades (
            trade_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            venue TEXT NOT NULL DEFAULT 'kalshi',
            resolved INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def _insert(
    path: Path,
    trade_id: str,
    venue: str,
    ticker: str,
    *,
    resolved: int = 0,
    venue_market_id: str | None = None,
    identity_status: str | None = None,
    quarantine_reason: str | None = None,
) -> None:
    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
    if "venue_market_id" in columns:
        conn.execute(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                trade_id,
                ticker,
                venue,
                resolved,
                venue_market_id,
                identity_status,
                quarantine_reason,
            ),
        )
    else:
        conn.execute(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?)",
            (trade_id, ticker, venue, resolved),
        )
    conn.commit()
    conn.close()


class FakeResolver:
    def __init__(self, lookups: dict[tuple[Venue, str], IdentityLookup]):
        self.lookups = lookups
        self.calls: list[tuple[Venue, str]] = []

    def lookup(self, venue: Venue, alias: str) -> IdentityLookup:
        self.calls.append((venue, alias))
        return self.lookups[(venue, alias)]


def _rows(path: Path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT trade_id, venue, ticker, resolved, venue_market_id, "
            "identity_status, quarantine_reason FROM paper_trades ORDER BY trade_id"
        ).fetchall()
    finally:
        conn.close()


def _artifact_hashes(path: Path) -> dict[str, str]:
    result = {}
    for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if candidate.exists():
            result[candidate.name] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return result


def _http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(response=response)


def test_fresh_ddl_has_nullable_constrained_identity_columns():
    conn = sqlite3.connect(":memory:")
    for statement in _DDL.split(";"):
        if statement.strip():
            conn.execute(statement)
    info = {row[1]: row for row in conn.execute("PRAGMA table_info(paper_trades)")}
    assert info["venue_market_id"][3] == 0
    assert info["identity_status"][3] == 0
    assert info["quarantine_reason"][3] == 0
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO paper_trades (trade_id, ts, ticker, venue, market_title, side, "
            "contracts, price_cents, cost_dollars, estimated_prob, entry_price_cents, edge, "
            "kelly_dollars, capped_dollars, signal_headline, signal_source, keywords_matched, "
            "reasoning, identity_status) VALUES "
            "('bad','t','KX','kalshi','m','yes',1,1,0.01,0.5,1,0,0,0,'h','s','[]','r','bad')"
        )


def test_paper_trader_startup_does_not_add_identity_columns(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    old_ddl = _DDL.replace(
        """    venue_market_id         TEXT,
    identity_status         TEXT CHECK (
        identity_status IS NULL OR identity_status IN ('mapped', 'quarantined')
    ),
    quarantine_reason       TEXT,
""",
        "",
    )
    conn = sqlite3.connect(db)
    for statement in old_ddl.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.commit()
    conn.close()
    monkeypatch.setattr("trading.paper_trader.SourceCredibility", MagicMock())
    trader = PaperTrader(db_path=db, startup_context="test")
    assert not {"venue_market_id", "identity_status", "quarantine_reason"} & trader._paper_trades_columns()


def test_public_client_paginates_to_return_all_exact_candidates():
    client = PolymarketPublicClient(base_url="https://example.invalid")
    client._request = MagicMock(
        side_effect=[
            {
                "markets": [
                    {"slug": "same-slug", "id": 11},
                    {"slug": "different", "id": 12},
                ],
                "cursor": "next-page",
            },
            {"markets": [{"slug": "same-slug", "id": 13}], "cursor": None},
        ]
    )
    assert client.find_market_payloads_by_slug_or_id("same-slug") == (
        {"slug": "same-slug", "id": 11},
        {"slug": "same-slug", "id": 13},
    )
    assert client._request.call_args_list[1].kwargs["params"]["cursor"] == "next-page"


@pytest.mark.parametrize(
    "response",
    [
        [],
        {},
        {"markets": {}},
        {"markets": [], "cursor": 123},
        {"markets": [None], "cursor": None},
    ],
)
def test_public_client_rejects_malformed_exact_filter_pages(response):
    client = PolymarketPublicClient(base_url="https://example.invalid")
    client._request = MagicMock(return_value=response)
    with pytest.raises(ValueError, match="Polymarket exact-filter"):
        client.find_market_payloads_by_slug_or_id("same-slug")


def test_public_client_rejects_exact_filter_cursor_cycle():
    client = PolymarketPublicClient(base_url="https://example.invalid")
    client._request = MagicMock(
        side_effect=[
            {"markets": [], "cursor": "repeat"},
            {"markets": [], "cursor": "repeat"},
        ]
    )
    with pytest.raises(ValueError, match="cursor cycle"):
        client.find_market_payloads_by_slug_or_id("same-slug")


def test_public_client_rejects_exact_filter_page_bound(monkeypatch):
    client = PolymarketPublicClient(base_url="https://example.invalid")
    monkeypatch.setattr(client, "_EXACT_FILTER_MAX_PAGES", 2, raising=False)
    client._request = MagicMock(
        side_effect=[
            {"markets": [], "cursor": "page-2"},
            {"markets": [], "cursor": "page-3"},
        ]
    )
    with pytest.raises(ValueError, match="page limit"):
        client.find_market_payloads_by_slug_or_id("same-slug")


def test_public_client_does_not_return_partial_exact_filter_results():
    client = PolymarketPublicClient(base_url="https://example.invalid")
    client._request = MagicMock(
        side_effect=[
            {
                "markets": [{"slug": "same-slug", "id": 11}],
                "cursor": "next-page",
            },
            requests.Timeout("timed out"),
        ]
    )
    with pytest.raises(requests.Timeout):
        client.find_market_payloads_by_slug_or_id("same-slug")


def test_kalshi_exact_lookup_maps_only_authoritative_404_to_missing():
    client = KalshiRestClient()
    client._request = MagicMock(side_effect=_http_error(404))
    assert client.get_market_exact("KX-EXACT") is None

    client._request.side_effect = _http_error(503)
    with pytest.raises(requests.HTTPError):
        client.get_market_exact("KX-EXACT")

    client._request.side_effect = requests.Timeout("timed out")
    with pytest.raises(requests.Timeout):
        client.get_market_exact("KX-EXACT")


@pytest.mark.parametrize(
    "failure",
    [
        ValueError("malformed response"),
        UnsupportedPayloadContractError("unsupported response", ticker="KX-EXACT"),
    ],
    ids=["malformed", "unsupported"],
)
def test_kalshi_exact_lookup_preserves_normalization_failures(monkeypatch, failure):
    client = KalshiRestClient()
    client._request = MagicMock(return_value={"market": {"ticker": "KX-EXACT"}})
    normalizer = MagicMock(side_effect=failure)
    monkeypatch.setattr("kalshi.rest_client.normalize_market_detail", normalizer)

    with pytest.raises(type(failure), match=str(failure)):
        client.get_market_exact("KX-EXACT")

    normalizer.assert_called_once_with({"market": {"ticker": "KX-EXACT"}})


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("timed out"),
        _http_error(503),
        ValueError("malformed response"),
        UnsupportedPayloadContractError("unsupported response", ticker="KX-EXACT"),
    ],
    ids=["timeout", "http-5xx", "malformed", "unsupported"],
)
def test_repeated_kalshi_failures_never_become_quarantine(tmp_path, failure):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "k1", "kalshi", "KX-EXACT")
    kalshi = MagicMock()
    kalshi.get_market_exact.side_effect = [failure, failure]
    resolver = PublicVenueIdentityResolver(kalshi=kalshi, polymarket=MagicMock())
    with open_readonly(db) as conn:
        first = plan_identity_migration(conn, resolver)
    with open_readonly(db) as conn:
        second = plan_identity_migration(
            conn,
            resolver,
            reviewed_plan_fingerprint=first.fingerprint,
        )
    assert [row.trade_id for row in second.unresolved] == ["k1"]
    assert not second.quarantine


@pytest.mark.parametrize(
    ("payloads", "kind", "canonical_id"),
    [
        (({"slug": "election-slug", "id": 44051},), "mapped", "44051"),
        ((), "missing", None),
        (
            (
                {"slug": "election-slug", "id": 44051},
                {"slug": "election-slug", "id": 44052},
            ),
            "ambiguous",
            None,
        ),
        (({"slug": "different", "id": 44051},), "mismatch", None),
        (({"slug": "election-slug", "id": "not-numeric"},), "mismatch", None),
    ],
)
def test_public_resolver_requires_unique_exact_pm_slug_and_numeric_id(
    payloads,
    kind,
    canonical_id,
):
    polymarket = MagicMock()
    polymarket.find_market_payloads_by_slug_or_id.return_value = payloads
    resolver = PublicVenueIdentityResolver(kalshi=MagicMock(), polymarket=polymarket)
    result = resolver.lookup(Venue.POLYMARKET_US, "election-slug")
    assert (result.kind, result.canonical_id) == (kind, canonical_id)


def test_public_resolver_maps_short_exact_pm_page_without_cursor():
    polymarket = PolymarketPublicClient(base_url="https://example.invalid")
    polymarket._request = MagicMock(
        return_value={
            "markets": [{"slug": "election-slug", "id": 44051}],
        }
    )
    resolver = PublicVenueIdentityResolver(
        kalshi=MagicMock(),
        polymarket=polymarket,
    )

    result = resolver.lookup(Venue.POLYMARKET_US, "election-slug")

    assert (result.kind, result.canonical_id) == (
        "mapped",
        "44051",
    )


@pytest.mark.parametrize(
    ("market", "kind"),
    [
        (SimpleNamespace(ticker="KX-EXACT"), "mapped"),
        (SimpleNamespace(ticker="KX-DIFFERENT"), "mismatch"),
        (None, "missing"),
    ],
)
def test_public_resolver_requires_exact_kalshi_ticker(market, kind):
    kalshi = MagicMock()
    kalshi.get_market_exact.return_value = market
    resolver = PublicVenueIdentityResolver(kalshi=kalshi, polymarket=MagicMock())
    assert resolver.lookup(Venue.KALSHI, "KX-EXACT").kind == kind


def test_public_resolver_transport_failure_is_not_quarantine():
    polymarket = MagicMock()
    polymarket.find_market_payloads_by_slug_or_id.side_effect = TimeoutError
    resolver = PublicVenueIdentityResolver(kalshi=MagicMock(), polymarket=polymarket)
    assert resolver.lookup(Venue.POLYMARKET_US, "slug").kind == "transport"


def test_dry_run_is_byte_for_byte_read_only_and_groups_duplicate_lots(tmp_path):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "k1", "kalshi", "KX-SAME")
    _insert(db, "k2", "kalshi", "KX-SAME")
    before = _artifact_hashes(db)
    resolver = FakeResolver(
        {(Venue.KALSHI, "KX-SAME"): IdentityLookup.mapped("KX-SAME")}
    )
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    assert _artifact_hashes(db) == before
    assert resolver.calls == [(Venue.KALSHI, "KX-SAME")]
    assert [row.trade_id for row in plan.mapped] == ["k1", "k2"]


def test_slug_numeric_divergence_and_same_alias_cross_venue(tmp_path):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "p1", "polymarket_us", "shared")
    _insert(db, "k1", "kalshi", "shared")
    resolver = FakeResolver(
        {
            (Venue.POLYMARKET_US, "shared"): IdentityLookup.mapped("44051"),
            (Venue.KALSHI, "shared"): IdentityLookup.mapped("shared"),
        }
    )
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    apply_identity_plan(db, plan, reviewed_plan_fingerprint=plan.fingerprint)
    assert _rows(db) == [
        ("k1", "kalshi", "shared", 0, "shared", "mapped", None),
        ("p1", "polymarket_us", "shared", 0, "44051", "mapped", None),
    ]


def test_eleven_unique_polymarket_aliases_map_to_numeric_ids(tmp_path):
    db = tmp_path / "paper.db"
    _create_db(db)
    lookups = {}
    for index in range(11):
        alias = f"election-market-{index}"
        _insert(db, f"p{index:02d}", "polymarket_us", alias)
        lookups[(Venue.POLYMARKET_US, alias)] = IdentityLookup.mapped(str(44000 + index))
    resolver = FakeResolver(lookups)
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    assert len(plan.mapped) == 11
    assert len(resolver.calls) == 11
    assert {row.target_market_id for row in plan.mapped} == {
        str(44000 + index) for index in range(11)
    }


@pytest.mark.parametrize("kind", ["missing", "transport"])
def test_missing_and_transport_remain_unwritten(tmp_path, kind):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "p1", "polymarket_us", "slug")
    lookup = IdentityLookup.missing() if kind == "missing" else IdentityLookup.transport("timeout")
    resolver = FakeResolver({(Venue.POLYMARKET_US, "slug"): lookup})
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    apply_identity_plan(db, plan, reviewed_plan_fingerprint=plan.fingerprint)
    assert _rows(db) == [("p1", "polymarket_us", "slug", 0, None, None, None)]


@pytest.mark.parametrize("lookup", [
    IdentityLookup.ambiguous(),
    IdentityLookup.mismatch("returned_alias_mismatch"),
])
def test_deterministic_conflicts_require_explicit_quarantine(tmp_path, lookup):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "p1", "polymarket_us", "slug")
    resolver = FakeResolver({(Venue.POLYMARKET_US, "slug"): lookup})
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    apply_identity_plan(db, plan, reviewed_plan_fingerprint=plan.fingerprint)
    assert _rows(db)[0][-2:] == (None, None)
    with pytest.raises(ValueError, match="reviewed plan fingerprint"):
        apply_identity_plan(db, plan, apply_quarantine=True)
    apply_identity_plan(
        db,
        plan,
        apply_quarantine=True,
        reviewed_plan_fingerprint=plan.fingerprint,
    )
    assert _rows(db)[0][-2] == "quarantined"


def test_unsupported_venue_is_quarantine_candidate_without_adapter_call(tmp_path):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "x1", "unsupported", "alias")
    resolver = FakeResolver({})
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    assert resolver.calls == []
    assert plan.quarantine[0].target_reason == "unsupported_venue"


def test_repeated_reviewed_absence_can_be_quarantined(tmp_path):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "p1", "polymarket_us", "slug")
    resolver = FakeResolver({(Venue.POLYMARKET_US, "slug"): IdentityLookup.missing()})
    with open_readonly(db) as conn:
        first = plan_identity_migration(conn, resolver)
    with open_readonly(db) as conn:
        repeated = plan_identity_migration(conn, resolver, reviewed_plan_fingerprint=first.fingerprint)
    assert repeated.quarantine
    apply_identity_plan(
        db,
        repeated,
        apply_quarantine=True,
        reviewed_plan_fingerprint=repeated.fingerprint,
    )
    assert _rows(db)[0][-2:] == ("quarantined", "confirmed_absence")


def test_existing_same_is_noop_conflicting_id_never_overwritten(tmp_path):
    db = tmp_path / "paper.db"
    _create_db(db, with_identity=True)
    _insert(db, "same", "polymarket_us", "slug", venue_market_id="11", identity_status="mapped")
    _insert(db, "conflict", "polymarket_us", "other", venue_market_id="old", identity_status="mapped")
    resolver = FakeResolver(
        {
            (Venue.POLYMARKET_US, "slug"): IdentityLookup.mapped("11"),
            (Venue.POLYMARKET_US, "other"): IdentityLookup.mapped("22"),
        }
    )
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    assert [row.trade_id for row in plan.unchanged] == ["same"]
    assert [row.trade_id for row in plan.quarantine] == ["conflict"]
    apply_identity_plan(db, plan, reviewed_plan_fingerprint=plan.fingerprint)
    assert dict((row[0], row[4]) for row in _rows(db)) == {"conflict": "old", "same": "11"}


def test_settled_rows_untouched_and_apply_is_idempotent(tmp_path):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "settled", "kalshi", "KX-OLD", resolved=1)
    _insert(db, "open", "kalshi", "KX-OPEN")
    resolver = FakeResolver({(Venue.KALSHI, "KX-OPEN"): IdentityLookup.mapped("KX-OPEN")})
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    apply_identity_plan(db, plan, reviewed_plan_fingerprint=plan.fingerprint)
    first = _rows(db)
    with open_readonly(db) as conn:
        retry = plan_identity_migration(conn, resolver)
    apply_identity_plan(db, retry, reviewed_plan_fingerprint=retry.fingerprint)
    assert _rows(db) == first
    assert next(row for row in first if row[0] == "settled")[4:] == (None, None, None)


@pytest.mark.parametrize(
    "fail_stage",
    [
        "after_ddl:venue_market_id",
        "after_ddl:identity_status",
        "after_ddl:quarantine_reason",
        "after_ddl",
        "after_update",
    ],
)
def test_apply_rolls_back_ddl_and_updates_on_fault(tmp_path, fail_stage):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "p1", "polymarket_us", "slug")
    resolver = FakeResolver({(Venue.POLYMARKET_US, "slug"): IdentityLookup.mapped("11")})
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    with pytest.raises(RuntimeError, match="injected"):
        apply_identity_plan(
            db,
            plan,
            reviewed_plan_fingerprint=plan.fingerprint,
            fault_hook=lambda stage: (_ for _ in ()).throw(RuntimeError("injected"))
            if stage == fail_stage
            else None,
        )
    conn = sqlite3.connect(db)
    assert "venue_market_id" not in {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
    conn.close()


@pytest.mark.parametrize(
    "lookup",
    [IdentityLookup.mapped("11"), IdentityLookup.missing()],
)
def test_apply_aborts_on_database_drift(tmp_path, lookup):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "p1", "polymarket_us", "slug")
    resolver = FakeResolver({(Venue.POLYMARKET_US, "slug"): lookup})
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE paper_trades SET ticker='changed' WHERE trade_id='p1'")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="database drift"):
        apply_identity_plan(db, plan, reviewed_plan_fingerprint=plan.fingerprint)


@pytest.mark.parametrize("provided", [None, "wrong-fingerprint"])
def test_apply_rejects_unreviewed_plan_before_opening_database(
    tmp_path,
    monkeypatch,
    provided,
):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "k1", "kalshi", "KX-EXACT")
    resolver = FakeResolver({(Venue.KALSHI, "KX-EXACT"): IdentityLookup.mapped("KX-EXACT")})
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    connect = MagicMock(side_effect=AssertionError("database must not be opened"))
    monkeypatch.setattr("scripts.migrate_paper_market_identity.sqlite3.connect", connect)
    with pytest.raises(ValueError, match="reviewed plan fingerprint"):
        apply_identity_plan(db, plan, reviewed_plan_fingerprint=provided)
    connect.assert_not_called()


def test_apply_aborts_when_open_row_is_inserted_after_plan(tmp_path):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "k1", "kalshi", "KX-EXACT")
    resolver = FakeResolver({(Venue.KALSHI, "KX-EXACT"): IdentityLookup.mapped("KX-EXACT")})
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    _insert(db, "k2", "kalshi", "KX-INSERTED")

    with pytest.raises(RuntimeError, match="database drift"):
        apply_identity_plan(db, plan, reviewed_plan_fingerprint=plan.fingerprint)

    conn = sqlite3.connect(db)
    assert "venue_market_id" not in {
        row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")
    }
    conn.close()


@pytest.mark.parametrize("marker", ["?", "#", "%"])
def test_database_paths_are_uri_safe_and_do_not_create_other_artifacts(tmp_path, marker):
    db = tmp_path / f"paper{marker}identity.db"
    _create_db(db)
    _insert(db, "k1", "kalshi", "KX-EXACT")
    before_names = {path.name for path in tmp_path.iterdir()}
    resolver = FakeResolver({(Venue.KALSHI, "KX-EXACT"): IdentityLookup.mapped("KX-EXACT")})
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    assert {path.name for path in tmp_path.iterdir()} == before_names

    apply_identity_plan(db, plan, reviewed_plan_fingerprint=plan.fingerprint)

    assert _rows(db)[0][4:6] == ("KX-EXACT", "mapped")
    assert {path.name for path in tmp_path.iterdir()} == before_names


@pytest.mark.parametrize("mode", ["--apply", "--apply-quarantine"])
def test_cli_write_modes_require_reviewed_plan_fingerprint(monkeypatch, mode):
    monkeypatch.setattr(sys, "argv", ["migrate_paper_market_identity.py", mode])
    with pytest.raises(SystemExit):
        _parse_args()


def test_cli_binds_provided_fingerprint_to_plan_and_apply(tmp_path, monkeypatch):
    db = tmp_path / "paper.db"
    _create_db(db)
    args = SimpleNamespace(
        db=db,
        apply=True,
        apply_quarantine=False,
        reviewed_plan_fingerprint="reviewed-fingerprint",
    )
    plan = MagicMock(fingerprint="reviewed-fingerprint")
    plan.to_json.return_value = "{}"
    planner = MagicMock(return_value=plan)
    applier = MagicMock()
    monkeypatch.setattr("scripts.migrate_paper_market_identity._parse_args", lambda: args)
    monkeypatch.setattr("scripts.migrate_paper_market_identity.PublicVenueIdentityResolver", MagicMock())
    monkeypatch.setattr("scripts.migrate_paper_market_identity.plan_identity_migration", planner)
    monkeypatch.setattr("scripts.migrate_paper_market_identity.apply_identity_plan", applier)

    assert main() == 0

    assert planner.call_args.kwargs["reviewed_plan_fingerprint"] == "reviewed-fingerprint"
    assert applier.call_args.kwargs["reviewed_plan_fingerprint"] == "reviewed-fingerprint"


def test_plan_json_is_deterministic_and_payload_free(tmp_path):
    db = tmp_path / "paper.db"
    _create_db(db)
    _insert(db, "p1", "polymarket_us", "slug")
    resolver = FakeResolver({(Venue.POLYMARKET_US, "slug"): IdentityLookup.mapped("11")})
    with open_readonly(db) as conn:
        plan = plan_identity_migration(conn, resolver)
    encoded = plan.to_json()
    assert encoded == json.dumps(json.loads(encoded), sort_keys=True, separators=(",", ":"))
    assert "payload" not in encoded.lower()
