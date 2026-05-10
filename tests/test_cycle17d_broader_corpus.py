from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.edge_replay.build_cycle17d_broader_corpus import (
    DEFAULT_COHORT,
    build_cycle17d_broader_corpus,
    filter_markets,
    main,
)
from tests._helpers import write_jsonl


def _market(ticker: str, **overrides) -> dict:
    row = {
        "ticker": ticker,
        "title": f"{ticker} title",
        "series_ticker": ticker.split("-")[0],
        "status": "settled",
        "resolved_yes": True,
        "result": "yes",
        "yes_price": 50.0,
        "close_time": "2026-03-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _make_evidence_db(path: Path, ticker: str = "KXGEO-26MAR01", *, append: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        if not append:
            conn.executescript(
                """
                CREATE TABLE evidence (
                    evidence_id TEXT,
                    market_ticker TEXT,
                    source TEXT,
                    source_class TEXT,
                    headline TEXT,
                    ingested_ts TEXT,
                    update_type TEXT
                );
                CREATE TABLE dossier_updates (
                    market_ticker TEXT,
                    dossier_version INTEGER,
                    created_ts TEXT,
                    trigger_evidence_id TEXT,
                    prior_estimate REAL,
                    new_estimate REAL,
                    update_delta REAL,
                    confidence_before REAL,
                    confidence_after REAL,
                    update_type TEXT,
                    llm_called INTEGER
                );
                """
            )
        evidence_id = f"ev-{ticker}"
        conn.execute(
            """
            INSERT INTO evidence VALUES (
                ?, ?, 'Reuters', 'publisher_rss', 'Court order published',
                '2026-03-01T00:00:00+00:00', 'news'
            )
            """,
            (evidence_id, ticker),
        )
        conn.execute(
            """
            INSERT INTO dossier_updates VALUES (
                ?, 1, '2026-03-01T00:05:00+00:00', ?,
                0.50, 0.70, 0.20, 0.20, 0.95, 'news', 1
            )
            """,
            (ticker, evidence_id),
        )
        conn.commit()
    finally:
        conn.close()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_filter_markets_applies_evidence_window_and_sports_blocklist():
    markets = [
        _market("KXGEO-26MAR01"),
        _market("KXOTHER-26MAR01"),
        _market("KXOLD-26JAN01", close_time="2025-12-31T23:59:59Z"),
        _market("KXNFLGAME-26MAR01", series_ticker="KXNFL"),
    ]

    rows, counts = filter_markets(
        markets,
        evidence_tickers={"KXGEO-26MAR01", "KXOLD-26JAN01", "KXNFLGAME-26MAR01"},
    )

    assert [row["ticker"] for row in rows] == ["KXGEO-26MAR01"]
    assert counts == {
        "excluded_not_in_evidence_store": 1,
        "excluded_outside_close_time_window": 1,
        "excluded_sports_prefix_blocklist": 1,
        "post_market_filters": 1,
        "pre_filter": 4,
    }


def test_build_broader_corpus_writes_dataset_manifest_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.edge_replay.build_cycle17d_broader_corpus._git_head", lambda: "abc123")
    monkeypatch.setattr("scripts.edge_replay.build_cycle17d_broader_corpus._blocklist_commit", lambda: "def456")
    evidence_db = tmp_path / "evidence_store.db"
    paper_db = tmp_path / "paper_trades.db"
    _make_evidence_db(evidence_db)
    markets = [
        _market("KXGEO-26MAR01", series_ticker="KXGEO"),
        _market("KXNOPRICE-26MAR01", series_ticker="KXNOPRICE"),
    ]
    prices = {
        "KXGEO-26MAR01": [{"ts": "2026-03-01T00:01:00+00:00", "yes_price": 44.0}],
    }
    output = tmp_path / "replay_dataset_broader.jsonl"
    manifest_path = tmp_path / "build_manifest.json"
    markets_output = tmp_path / "resolved_markets.json"
    prices_output = tmp_path / "historical_prices.json"
    price_errors_output = tmp_path / "historical_price_errors.json"

    manifest = build_cycle17d_broader_corpus(
        markets=markets,
        historical_prices=prices,
        evidence_tickers={"KXGEO-26MAR01", "KXNOPRICE-26MAR01"},
        paper_trades_db=paper_db,
        evidence_store_db=evidence_db,
        trade_logs=[],
        output=output,
        manifest_path=manifest_path,
        markets_output=markets_output,
        prices_output=prices_output,
        price_errors_output=price_errors_output,
        price_errors={"KXNOPRICE-26MAR01": [{"error": "no price rows returned"}]},
    )

    rows = _read_jsonl(output)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "KXGEO-26MAR01"
    assert rows[0]["cohort"] == DEFAULT_COHORT
    assert rows[0]["market_family"] == "KXGEO"
    assert rows[0]["market_yes_price"] == 44.0
    assert rows[0]["edge"] == pytest.approx(0.26)
    assert rows[0]["confidence"] == 0.95
    assert rows[0]["model_prob"] == 0.70
    assert rows[0]["signal_source"] == "Reuters"
    assert rows[0]["signal_type"] == "news"
    assert rows[0]["news_class"] == "publisher_rss"
    assert rows[0]["headline"] == "Court order published"

    assert manifest["row_count"] == 1
    assert manifest["market_count"] == 1
    assert manifest["cohort_counts"] == {DEFAULT_COHORT: 1}
    assert manifest["filter_counts"] == {
        "excluded_without_historical_price_coverage": 1,
        "post_dataset_build_rows": 1,
        "post_market_filters": 2,
        "post_price_coverage": 1,
        "pre_filter": 2,
        "pre_price_coverage": 2,
    }
    assert manifest["blocklist"] == {
        "current_head": "abc123",
        "prefix_count": manifest["blocklist"]["prefix_count"],
        "source": "config.MARKET_SERIES_BLOCKLIST_PREFIXES",
        "source_commit": "def456",
    }
    assert manifest["scorer_schema_compatibility"] == {
        "audit_script": "scripts/edge_replay/cycle17d_schema_audit.py",
        "status": "ready-for-audit",
    }
    assert all(value["fraction"] == 1.0 for value in manifest["required_field_completeness"].values())
    assert manifest["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    assert json.loads(markets_output.read_text(encoding="utf-8")) == [markets[0]]
    assert json.loads(prices_output.read_text(encoding="utf-8")) == prices
    assert json.loads(price_errors_output.read_text(encoding="utf-8")) == {
        "KXNOPRICE-26MAR01": [{"error": "no price rows returned"}]
    }


def test_broader_corpus_cli_supports_saved_inputs(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("scripts.edge_replay.build_cycle17d_broader_corpus._git_head", lambda: "abc123")
    monkeypatch.setattr("scripts.edge_replay.build_cycle17d_broader_corpus._blocklist_commit", lambda: "def456")
    evidence_db = tmp_path / "evidence_store.db"
    paper_db = tmp_path / "paper_trades.db"
    _make_evidence_db(evidence_db)
    markets_json = tmp_path / "markets.json"
    prices_json = tmp_path / "prices.json"
    output = tmp_path / "out.jsonl"
    manifest_path = tmp_path / "manifest.json"

    markets_json.write_text(json.dumps({"markets": [_market("KXGEO-26MAR01", series_ticker="KXGEO")]}), encoding="utf-8")
    prices_json.write_text(
        json.dumps({"KXGEO-26MAR01": [{"ts": "2026-03-01T00:01:00+00:00", "yes_price": 44.0}]}),
        encoding="utf-8",
    )
    write_jsonl(tmp_path / "empty.jsonl", [])

    status = main(
        [
            "--markets-json",
            str(markets_json),
            "--historical-prices-json",
            str(prices_json),
            "--evidence-store-db",
            str(evidence_db),
            "--paper-trades-db",
            str(paper_db),
            "--trade-log",
            str(tmp_path / "empty.jsonl"),
            "--output",
            str(output),
            "--manifest",
            str(manifest_path),
            "--markets-output",
            str(tmp_path / "resolved_markets.json"),
            "--historical-prices-output",
            str(tmp_path / "historical_prices.json"),
            "--historical-price-errors-output",
            str(tmp_path / "historical_price_errors.json"),
            "--json",
        ]
    )

    printed = json.loads(capsys.readouterr().out)
    assert status == 0
    assert printed["row_count"] == 1
    assert printed["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_cli_live_fetch_keeps_evidence_intersection_as_manifest_filter(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("scripts.edge_replay.build_cycle17d_broader_corpus._git_head", lambda: "abc123")
    monkeypatch.setattr("scripts.edge_replay.build_cycle17d_broader_corpus._blocklist_commit", lambda: "def456")
    evidence_db = tmp_path / "evidence_store.db"
    paper_db = tmp_path / "paper_trades.db"
    _make_evidence_db(evidence_db)
    output = tmp_path / "out.jsonl"
    manifest_path = tmp_path / "manifest.json"

    class Client:
        def get_markets(self, *, status, limit, cursor, min_close_ts=None, max_close_ts=None):
            assert status == "settled"
            assert min_close_ts is not None
            assert max_close_ts is not None
            return [
                SimpleNamespace(
                    ticker="KXGEO-26MAR01",
                    title="geo",
                    series_ticker="KXGEO",
                    status="settled",
                    result="yes",
                    yes_price=50,
                    close_time="2026-03-01T00:00:00Z",
                ),
                SimpleNamespace(
                    ticker="KXOTHER-26MAR01",
                    title="other",
                    series_ticker="KXOTHER",
                    status="settled",
                    result="yes",
                    yes_price=50,
                    close_time="2026-03-01T00:00:00Z",
                ),
            ], None

        def _request(self, method, endpoint, params=None):
            assert method == "GET"
            assert params["ticker"] == "KXGEO-26MAR01"
            if endpoint == "/markets/trades":
                return {"trades": []}
            if endpoint == "/historical/trades":
                return {"trades": [{"created_time": "2026-03-01T00:01:00+00:00", "yes_price": 44.0}]}
            raise AssertionError(endpoint)

    monkeypatch.setattr("kalshi.rest_client.KalshiRestClient", Client)
    write_jsonl(tmp_path / "empty.jsonl", [])

    status = main(
        [
            "--evidence-store-db",
            str(evidence_db),
            "--paper-trades-db",
            str(paper_db),
            "--trade-log",
            str(tmp_path / "empty.jsonl"),
            "--output",
            str(output),
            "--manifest",
            str(manifest_path),
            "--markets-output",
            str(tmp_path / "resolved_markets.json"),
            "--historical-prices-output",
            str(tmp_path / "historical_prices.json"),
            "--historical-price-errors-output",
            str(tmp_path / "historical_price_errors.json"),
            "--json",
        ]
    )

    printed = json.loads(capsys.readouterr().out)
    assert status == 0
    assert printed["filter_counts"]["pre_filter"] == 2
    assert printed["filter_counts"]["excluded_not_in_evidence_store"] == 1
    assert printed["row_count"] == 1


def test_cli_live_fetch_falls_back_by_ticker_when_list_has_no_evidence_match(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("scripts.edge_replay.build_cycle17d_broader_corpus._git_head", lambda: "abc123")
    monkeypatch.setattr("scripts.edge_replay.build_cycle17d_broader_corpus._blocklist_commit", lambda: "def456")
    evidence_db = tmp_path / "evidence_store.db"
    paper_db = tmp_path / "paper_trades.db"
    _make_evidence_db(evidence_db)
    output = tmp_path / "out.jsonl"
    manifest_path = tmp_path / "manifest.json"

    class Client:
        def get_markets(self, *, status, limit, cursor, min_close_ts=None, max_close_ts=None):
            return [
                SimpleNamespace(
                    ticker="KXOTHER-26MAR01",
                    title="other",
                    series_ticker="KXOTHER",
                    status="settled",
                    result="yes",
                    yes_price=50,
                    close_time="2026-03-01T00:00:00Z",
                )
            ], None

        def get_market(self, ticker):
            assert ticker == "KXGEO-26MAR01"
            return SimpleNamespace(
                ticker="KXGEO-26MAR01",
                title="geo",
                series_ticker="KXGEO",
                status="finalized",
                result="yes",
                yes_price=50,
                close_time="2026-03-01T00:00:00Z",
            )

        def _request(self, method, endpoint, params=None):
            assert method == "GET"
            assert params["ticker"] == "KXGEO-26MAR01"
            if endpoint == "/markets/trades":
                return {"trades": []}
            if endpoint == "/historical/trades":
                return {"trades": [{"created_time": "2026-03-01T00:01:00+00:00", "yes_price": 44.0}]}
            raise AssertionError(endpoint)

    monkeypatch.setattr("kalshi.rest_client.KalshiRestClient", Client)
    write_jsonl(tmp_path / "empty.jsonl", [])

    status = main(
        [
            "--evidence-store-db",
            str(evidence_db),
            "--paper-trades-db",
            str(paper_db),
            "--trade-log",
            str(tmp_path / "empty.jsonl"),
            "--output",
            str(output),
            "--manifest",
            str(manifest_path),
            "--markets-output",
            str(tmp_path / "resolved_markets.json"),
            "--historical-prices-output",
            str(tmp_path / "historical_prices.json"),
            "--historical-price-errors-output",
            str(tmp_path / "historical_price_errors.json"),
            "--json",
        ]
    )

    printed = json.loads(capsys.readouterr().out)
    assert status == 0
    assert printed["filter_counts"]["pre_filter"] == 2
    assert printed["filter_counts"]["excluded_not_in_evidence_store"] == 1
    assert printed["raw_market_status_counts"] == {"finalized": 1, "settled": 1}
    assert printed["row_count"] == 1


def test_cli_live_fetch_falls_back_for_partial_evidence_underfetch(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("scripts.edge_replay.build_cycle17d_broader_corpus._git_head", lambda: "abc123")
    monkeypatch.setattr("scripts.edge_replay.build_cycle17d_broader_corpus._blocklist_commit", lambda: "def456")
    evidence_db = tmp_path / "evidence_store.db"
    paper_db = tmp_path / "paper_trades.db"
    _make_evidence_db(evidence_db, ticker="KXGEO1-26MAR01")
    _make_evidence_db(evidence_db, ticker="KXGEO2-26MAR01", append=True)
    output = tmp_path / "out.jsonl"
    manifest_path = tmp_path / "manifest.json"

    class Client:
        def get_markets(self, *, status, limit, cursor, min_close_ts=None, max_close_ts=None):
            return [
                SimpleNamespace(
                    ticker="KXGEO1-26MAR01",
                    title="geo 1",
                    series_ticker="KXGEO1",
                    status="settled",
                    result="yes",
                    yes_price=50,
                    close_time="2026-03-01T00:00:00Z",
                )
            ], None

        def get_market(self, ticker):
            assert ticker == "KXGEO2-26MAR01"
            return SimpleNamespace(
                ticker="KXGEO2-26MAR01",
                title="geo 2",
                series_ticker="KXGEO2",
                status="finalized",
                result="yes",
                yes_price=50,
                close_time="2026-03-01T00:00:00Z",
            )

        def _request(self, method, endpoint, params=None):
            assert method == "GET"
            ticker = params["ticker"]
            if endpoint == "/markets/trades":
                return {"trades": []}
            if endpoint == "/historical/trades":
                return {"trades": [{"created_time": "2026-03-01T00:01:00+00:00", "yes_price": 44.0}]}
            raise AssertionError(endpoint)

    monkeypatch.setattr("kalshi.rest_client.KalshiRestClient", Client)
    write_jsonl(tmp_path / "empty.jsonl", [])

    status = main(
        [
            "--evidence-store-db",
            str(evidence_db),
            "--paper-trades-db",
            str(paper_db),
            "--trade-log",
            str(tmp_path / "empty.jsonl"),
            "--output",
            str(output),
            "--manifest",
            str(manifest_path),
            "--markets-output",
            str(tmp_path / "resolved_markets.json"),
            "--historical-prices-output",
            str(tmp_path / "historical_prices.json"),
            "--historical-price-errors-output",
            str(tmp_path / "historical_price_errors.json"),
            "--json",
        ]
    )

    printed = json.loads(capsys.readouterr().out)
    assert status == 0
    assert printed["filter_counts"]["pre_filter"] == 2
    assert printed["filter_counts"]["post_market_filters"] == 2
    assert printed["raw_market_status_counts"] == {"finalized": 1, "settled": 1}
    assert printed["row_count"] == 2
