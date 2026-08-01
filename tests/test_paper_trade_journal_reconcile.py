import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.observability.paper_trade_journal_reconcile as paper_trade_journal_reconcile
from scripts.observability.paper_trade_journal_reconcile import (
    PaperTradeJournalReconciliationError,
    reconcile_paper_trade_journal,
)


TRADE_TS = "2026-07-31T12:00:00+00:00"


def _write_paper_db(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT,
                ts TEXT,
                ticker TEXT,
                venue TEXT,
                side TEXT,
                contracts INTEGER,
                price_cents INTEGER
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO paper_trades (
                trade_id, ts, ticker, venue, side, contracts, price_cents
            ) VALUES (
                :trade_id, :ts, :ticker, :venue, :side, :contracts, :price_cents
            )
            """,
            rows,
        )


def _paper_trade(
    trade_id: str,
    *,
    ticker: str = "KXTEST-31JUL",
    venue: str | None = "kalshi",
    side: str = "yes",
    contracts: int = 2,
    price_cents: int = 47,
    cohort_id: str | None = None,
    cohort_kind: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "type": "PAPER_TRADE",
        "trade_id": trade_id,
        "ts": TRADE_TS,
        "ticker": ticker,
        "side": side,
        "contracts": contracts,
        "price_cents": price_cents,
    }
    if venue is not None:
        record["venue"] = venue
    if cohort_id is not None:
        record["runtime_paper_cohort_id"] = cohort_id
    if cohort_kind is not None:
        record["runtime_paper_cohort_kind"] = cohort_kind
    return record


def _paper_row(trade_id: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "trade_id": trade_id,
        "ts": TRADE_TS,
        "ticker": "KXTEST-31JUL",
        "venue": "kalshi",
        "side": "yes",
        "contracts": 2,
        "price_cents": 47,
    }
    row.update(overrides)
    return row


def _write_records(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def test_reconcile_matches_archive_and_live_with_legacy_journal_venue_fallback(tmp_path: Path) -> None:
    trade_log_root = tmp_path / "logs" / "trades"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [_paper_row("archive-trade"), _paper_row("live-trade")])
    _write_records(
        trade_log_root / "archive" / "2026" / "07" / "2026-07-30.jsonl",
        [_paper_trade("archive-trade", venue=None)],
    )
    _write_records(
        trade_log_root / "live" / "trades.jsonl",
        [_paper_trade("live-trade")],
    )

    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=paper_db,
    )

    assert result.verdict == "OK"
    assert result.db_trade_rows_scanned == 2
    assert result.journal_rows_scanned == 2
    assert result.matched_count == 2
    assert result.db_only_count == 0
    assert result.journal_only_count == 0
    assert result.ambiguous_count == 0
    assert result.legacy_unmatchable_count == 0


def test_reconcile_reports_db_only_rows(tmp_path: Path) -> None:
    trade_log_root = tmp_path / "logs" / "trades"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [_paper_row("db-only")])
    trade_log_root.mkdir(parents=True)

    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=paper_db,
    )

    assert result.verdict == "ALARM_DB_ONLY"
    assert result.db_only_count == 1
    assert result.missing_in_journal_count == 1
    assert result.db_only_examples == ({"trade_id": "db-only"},)


def test_reconcile_rejects_a_missing_journal_root_instead_of_reporting_parity(tmp_path: Path) -> None:
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [_paper_row("db-row")])

    with pytest.raises(PaperTradeJournalReconciliationError, match="trade log path"):
        reconcile_paper_trade_journal(
            trade_log_root=tmp_path / "missing" / "trades",
            paper_db_path=paper_db,
        )


def test_reconcile_opens_the_paper_database_read_only(tmp_path: Path, monkeypatch) -> None:
    trade_log_root = tmp_path / "logs" / "trades"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [])
    trade_log_root.mkdir(parents=True)
    observed_connect_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    real_connect = sqlite3.connect

    def _observed_connect(*args, **kwargs):
        observed_connect_calls.append((args, kwargs))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(paper_trade_journal_reconcile.sqlite3, "connect", _observed_connect)
    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=paper_db,
    )

    assert result.verdict == "OK"
    assert len(observed_connect_calls) == 1
    connect_args, connect_kwargs = observed_connect_calls[0]
    assert "mode=ro" in str(connect_args[0])
    assert connect_kwargs["uri"] is True


def test_reconcile_fails_closed_for_a_db_row_without_timestamp_in_a_time_window(tmp_path: Path) -> None:
    trade_log_root = tmp_path / "logs" / "trades"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [_paper_row("missing-ts", ts="")])
    trade_log_root.mkdir(parents=True)

    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=paper_db,
        since=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )

    assert result.verdict == "ALARM_AMBIGUOUS"
    assert result.ambiguous_count == 1
    assert result.legacy_unmatchable_count == 0
    assert result.db_only_count == 0


def test_reconcile_reports_journal_only_rows(tmp_path: Path) -> None:
    trade_log_root = tmp_path / "logs" / "trades"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [])
    _write_records(
        trade_log_root / "live" / "trades.jsonl",
        [_paper_trade("journal-only")],
    )

    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=paper_db,
    )

    assert result.verdict == "ALARM_JOURNAL_ONLY"
    assert result.journal_only_count == 1
    assert result.missing_in_db_count == 1
    assert result.journal_only_examples == ({"trade_id": "journal-only"},)


def test_reconcile_ignores_non_paper_trade_journal_events(tmp_path: Path) -> None:
    trade_log_root = tmp_path / "logs" / "trades"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [])
    resolution_record = _paper_trade("resolution-only")
    resolution_record["type"] = "PAPER_RESOLUTION"
    _write_records(trade_log_root / "live" / "trades.jsonl", [resolution_record])

    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=paper_db,
    )

    assert result.verdict == "OK"
    assert result.journal_rows_scanned == 0
    assert result.journal_only_count == 0


def test_reconcile_flags_immutable_identity_mismatch_as_ambiguous(tmp_path: Path) -> None:
    trade_log_root = tmp_path / "logs" / "trades"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [_paper_row("mismatch")])
    _write_records(
        trade_log_root / "live" / "trades.jsonl",
        [_paper_trade("mismatch", price_cents=48)],
    )

    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=paper_db,
    )

    assert result.verdict == "ALARM_AMBIGUOUS"
    assert result.ambiguous_count == 1
    assert result.matched_count == 0
    assert result.ambiguous_examples[0]["trade_id"] == "mismatch"
    assert result.ambiguous_examples[0]["reason"] == "immutable_identity_mismatch"


def test_reconcile_alarms_when_a_durable_id_lacks_an_immutable_field(tmp_path: Path) -> None:
    trade_log_root = tmp_path / "logs" / "trades"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [_paper_row("incomplete-identity")])
    incomplete_journal_record = _paper_trade("incomplete-identity")
    incomplete_journal_record.pop("price_cents")
    _write_records(trade_log_root / "live" / "trades.jsonl", [incomplete_journal_record])

    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=paper_db,
    )

    assert result.verdict == "ALARM_AMBIGUOUS"
    assert result.ambiguous_count == 1
    assert result.legacy_unmatchable_count == 0
    assert result.matched_count == 0
    assert result.db_only_count == 0
    assert result.journal_only_count == 0


def test_reconcile_scoped_cohort_uses_only_exact_lineage_and_canonical_database(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    trade_log_root = repo_root / "logs" / "trades"
    canonical_db = repo_root / "data" / "paper_cohorts" / "cohort-a" / "paper_trades.db"
    _write_paper_db(canonical_db, [_paper_row("cohort-a-trade")])
    _write_records(
        trade_log_root / "live" / "trades.jsonl",
        [
            _paper_trade("cohort-a-trade", cohort_id="cohort-a", cohort_kind="active"),
            _paper_trade("other-cohort-trade", cohort_id="cohort-b", cohort_kind="active"),
        ],
    )

    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=canonical_db,
        repo_root=repo_root,
        runtime_paper_cohort_id="cohort-a",
        runtime_paper_cohort_kind="active",
    )

    assert result.verdict == "OK"
    assert result.journal_rows_scanned == 1
    assert result.journal_rows_excluded_outside_scope == 1
    assert result.matched_count == 1

    noncanonical_db = repo_root / "data" / "paper_trades.db"
    _write_paper_db(noncanonical_db, [_paper_row("cohort-a-trade")])
    with pytest.raises(PaperTradeJournalReconciliationError, match="canonical"):
        reconcile_paper_trade_journal(
            trade_log_root=trade_log_root,
            paper_db_path=noncanonical_db,
            repo_root=repo_root,
            runtime_paper_cohort_id="cohort-a",
            runtime_paper_cohort_kind="active",
        )


def test_reconcile_marks_legacy_rows_without_durable_trade_id_unmatchable(tmp_path: Path) -> None:
    trade_log_root = tmp_path / "logs" / "trades"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [])
    legacy_record = _paper_trade("discarded")
    legacy_record.pop("trade_id")
    _write_records(trade_log_root / "live" / "trades.jsonl", [legacy_record])

    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=paper_db,
    )

    assert result.verdict == "WARN_LEGACY_UNMATCHABLE"
    assert result.legacy_unmatchable_count == 1
    assert result.matched_count == 0
    assert result.db_only_count == 0
    assert result.journal_only_count == 0


def test_reconcile_never_fuzzy_matches_a_legacy_row_without_trade_id(tmp_path: Path) -> None:
    trade_log_root = tmp_path / "logs" / "trades"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_paper_db(paper_db, [_paper_row("durable-db-id")])
    legacy_record = _paper_trade("discarded")
    legacy_record.pop("trade_id")
    _write_records(trade_log_root / "live" / "trades.jsonl", [legacy_record])

    result = reconcile_paper_trade_journal(
        trade_log_root=trade_log_root,
        paper_db_path=paper_db,
    )

    assert result.verdict == "ALARM_DB_ONLY"
    assert result.matched_count == 0
    assert result.db_only_count == 1
    assert result.legacy_unmatchable_count == 1
