"""Contract tests for the runtime-bound replay corpus materializer."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import inspect
import json
import os
from pathlib import Path
import sqlite3

import pytest

from scripts.edge_replay import materialize_runtime_corpus
from scripts.edge_replay.oos_registry import OOSRegistrationAttestation
from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    FeeContext,
    FeeRole,
    KALSHI_GENERAL_2026_07_07,
    fee_coefficient_for,
    fee_type_for_schedule,
    quote_fee,
)
from trading.paper_accounting import (
    PAPER_ACCOUNTING_VERSION,
    PaperAccountingRecord,
    initialize_fresh_paper_accounting_schema,
)
from trading.paper_cohorts import (
    active_cohort_binding_for_db,
    initialize_active_paper_cohort_manifest,
    resolve_runtime_paper_cohort,
)
from trading.paper_trader import _DDL
from trading.runtime_paper_cohort_attestation import (
    build_runtime_paper_cohort_attestation,
    write_runtime_paper_cohort_attestation,
)
from trading.settlement_store import (
    enable_and_verify_foreign_keys,
    initialize_fresh_settlement_schema,
)


WINDOW_START = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)
MATERIALIZED_AT = datetime(2026, 7, 17, 0, 0, tzinfo=UTC)
REGISTRATION_ID = "profit-oos-runtime-a"
COHORT_ID = "active-20260714"


def _utc_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_registry(repo_root: Path, *, end_utc: datetime = WINDOW_END) -> Path:
    path = repo_root / "docs" / "governance" / "oos-corpus-registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    registration = {
        "id": REGISTRATION_ID,
        "declared_at_utc": "2026-07-01T00:00:00Z",
        "window": {
            "start_utc": _utc_text(WINDOW_START),
            "end_utc": _utc_text(end_utc),
        },
        "regime_label": "post_fee_net_active",
        "universe_policy": {
            "type": "market_families",
            "market_families": ["KXALPHA", "KXBETA"],
        },
        "exclude_contamination": True,
    }
    registration["registration_hash"] = hashlib.sha256(
        json.dumps(
            registration,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    path.write_text(
        json.dumps({"schema_version": 1, "registrations": [registration]}),
        encoding="utf-8",
    )
    return path


def _write_regimes_doc(repo_root: Path) -> Path:
    path = repo_root / "docs" / "governance" / "corpus-regimes.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """## Active regimes

| Label | Description | First declared |
|---|---|---|
| `post_fee_net_active` | Active fee-net replay cohort | 2026-07-01 |
""",
        encoding="utf-8",
    )
    return path


def _create_legacy_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO bot_state(key, value) VALUES ('notional_bankroll', '100')")
        conn.execute("CREATE TABLE paper_trades (resolved INTEGER NOT NULL)")


def _ensure_runtime_tables(conn: sqlite3.Connection) -> None:
    enable_and_verify_foreign_keys(conn)
    for statement in _DDL.split(";"):
        if statement.strip():
            conn.execute(statement)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}
    if "series_ticker" not in columns:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN series_ticker TEXT")
    if "cohort_extension" not in columns:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN cohort_extension TEXT")
    initialize_fresh_settlement_schema(conn, applied_at=MATERIALIZED_AT.isoformat())
    initialize_fresh_paper_accounting_schema(
        conn,
        migration_plan_sha256="a" * 64,
        applied_at=MATERIALIZED_AT.isoformat(),
    )


def _seed_trade(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    family: str,
    identity_status: str = "mapped",
    fee_net_accounting_version: int = PAPER_ACCOUNTING_VERSION,
) -> str:
    market_id = f"{family}-MKT"
    conn.execute(
        """
        INSERT INTO paper_trades (
            trade_id, ts, ticker, venue, venue_market_id, identity_status,
            fee_net_accounting_version,
            market_title, side, contracts, price_cents, cost_dollars,
            estimated_prob, entry_price_cents, edge, kelly_dollars,
            capped_dollars, signal_headline, signal_source,
            keywords_matched, reasoning
        ) VALUES (?, ?, ?, 'kalshi', ?, ?, ?,
                  'Test market', 'yes', 1, 45, 0.45, 0.55, 45, 0.10,
                  0.45, 0.45, 'headline', 'source', '[]', 'reason')
        """,
        (
            trade_id,
            _utc_text(WINDOW_START + timedelta(hours=1)),
            f"{family}-1",
            market_id,
            identity_status,
            fee_net_accounting_version,
        ),
    )
    conn.execute(
        "UPDATE paper_trades SET series_ticker = ?, cohort_extension = NULL WHERE trade_id = ?",
        (family, trade_id),
    )
    return market_id


def _seed_settlement_observation(
    conn: sqlite3.Connection,
    *,
    market_id: str,
    observation_sha256: str,
) -> None:
    now = MATERIALIZED_AT.isoformat()
    conn.execute(
        """
        INSERT INTO paper_settlement_observations (
            observation_sha256, venue, venue_market_id, alias, outcome,
            authoritative_outcome_json, canonical_payload_json, payload_sha256,
            observed_at, effective_at, rules_version, source_id,
            refund_cents_per_contract, refunds_entry_fee,
            supersedes_observation_sha256, applied_trade_count,
            bankroll_before_cents, gross_payout_cents, bankroll_after_cents,
            applied_at
        ) VALUES (?, 'kalshi', ?, ?, 'yes',
                  '{}', '{}', ?, ?, ?, 'test-v1', 'test',
                  NULL, NULL, NULL, 1, '0', '0', '0', ?)
        """,
        (
            observation_sha256,
            market_id,
            market_id,
            "c" * 64,
            now,
            now,
            now,
        ),
    )


def _seed_settled_accounting(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    observation_sha256: str,
    parent_resolved: int = 1,
    parent_settlement_observation_sha256: str | None = None,
    parent_settled_at: datetime | None = None,
) -> None:
    quantity = Decimal("1")
    price = Decimal("0.45")
    signed_revenue = -(quantity * price)
    coefficient = fee_coefficient_for(KALSHI_GENERAL_2026_07_07, FeeRole.TAKER)
    context = FeeContext(
        schedule_id=KALSHI_GENERAL_2026_07_07,
        role=FeeRole.TAKER,
        quantity=quantity,
        price=price,
        signed_revenue=signed_revenue,
        order_id=f"paper-order:{trade_id}",
        accumulator=Decimal("0"),
        multiplier=Decimal("1"),
        coefficient=coefficient,
        account_precision=DIRECT_ACCOUNT_PRECISION,
        timestamp=WINDOW_START + timedelta(hours=1),
    )
    quote = quote_fee(context)
    entry = PaperAccountingRecord(
        accounting_version=PAPER_ACCOUNTING_VERSION,
        entry_request_id=f"req-{trade_id}",
        trade_id=trade_id,
        order_id=context.order_id,
        fill_id=f"paper-fill:{trade_id}:0",
        filled_at=context.timestamp,
        schedule_id=context.schedule_id,
        role=context.role,
        quantity=context.quantity,
        price=context.price,
        signed_revenue=context.signed_revenue,
        multiplier=context.multiplier,
        fee_type=fee_type_for_schedule(context.schedule_id),
        fee_multiplier_provenance_sha256="d" * 64,
        fee_multiplier_effective_at=WINDOW_START,
        coefficient=context.coefficient,
        account_precision=context.account_precision,
        account_precision_mode="direct",
        quote=quote,
        gross_entry_debit=abs(signed_revenue),
        net_entry_debit=abs(signed_revenue) + quote.net_fee,
        recorded_at=context.timestamp,
    )
    settled = replace(
        entry,
        settlement_observation_sha256=observation_sha256,
        settled_at=MATERIALIZED_AT,
        settlement_fee=Decimal("0.01"),
        settlement_refund=Decimal("0"),
        gross_settlement_payout=Decimal("1"),
        net_settlement_payout=Decimal("0.99"),
        fee_net_pnl=Decimal("0.99") - entry.net_entry_debit,
    )
    values = settled.to_database_values()
    conn.execute(
        f"INSERT INTO paper_trade_accounting ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        tuple(values.values()),
    )
    conn.execute(
        "UPDATE paper_trades SET resolved = ?, settlement_observation_sha256 = ?, settled_at = ? WHERE trade_id = ?",
        (
            parent_resolved,
            parent_settlement_observation_sha256 or observation_sha256,
            (parent_settled_at or MATERIALIZED_AT).isoformat(),
            trade_id,
        ),
    )


def _snapshot_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_registration_attestor(registration, **_kwargs):
    return OOSRegistrationAttestation(
        registration_id=registration.id,
        registration_hash=registration.registration_hash,
        trusted_ref="origin/main",
        commit="a" * 40,
        integrated_at_utc="2026-07-02T00:00:00Z",
        registry_path="docs/governance/oos-corpus-registry.json",
    )


def _runtime_fixture(
    tmp_path: Path,
    *,
    settled_trades: tuple[tuple[str, str], ...] = (
        ("trade-alpha", "KXALPHA"),
        ("trade-beta", "KXBETA"),
    ),
    parent_overrides: dict[str, dict[str, object]] | None = None,
    missing_fee_net_field: bool = False,
) -> dict[str, Path]:
    repo_root = tmp_path / "repo"
    data_root = repo_root / "data"
    legacy_db = data_root / "paper_trades.db"
    _create_legacy_database(legacy_db)
    runtime = resolve_runtime_paper_cohort(
        COHORT_ID,
        legacy_starting_bankroll=100.0,
        active_starting_bankroll=100.0,
        db_root=data_root,
        cohort_kind="active",
    )
    initialize_active_paper_cohort_manifest(
        runtime,
        max_days_to_close=14.0,
        legacy_db_path=legacy_db,
        legacy_starting_bankroll=100.0,
    )
    with sqlite3.connect(runtime.db_path) as conn:
        _ensure_runtime_tables(conn)
        for trade_id, family in settled_trades:
            overrides = (parent_overrides or {}).get(trade_id, {})
            identity_status = overrides.get("identity_status", "mapped")
            fee_net_accounting_version = overrides.get("fee_net_accounting_version", PAPER_ACCOUNTING_VERSION)
            parent_resolved = overrides.get("resolved", 1)
            parent_observation_sha256 = overrides.get("settlement_observation_sha256")
            parent_settled_at = overrides.get("settled_at")
            assert isinstance(identity_status, str)
            assert isinstance(fee_net_accounting_version, int)
            assert isinstance(parent_resolved, int)
            assert parent_observation_sha256 is None or isinstance(parent_observation_sha256, str)
            assert parent_settled_at is None or isinstance(parent_settled_at, datetime)
            market_id = _seed_trade(
                conn,
                trade_id=trade_id,
                family=family,
                identity_status=identity_status,
                fee_net_accounting_version=fee_net_accounting_version,
            )
            observation_sha256 = hashlib.sha256(trade_id.encode("utf-8")).hexdigest()
            _seed_settlement_observation(
                conn,
                market_id=market_id,
                observation_sha256=observation_sha256,
            )
            if parent_observation_sha256 and parent_observation_sha256 != observation_sha256:
                _seed_settlement_observation(
                    conn,
                    market_id=market_id,
                    observation_sha256=parent_observation_sha256,
                )
            _seed_settled_accounting(
                conn,
                trade_id=trade_id,
                observation_sha256=observation_sha256,
                parent_resolved=parent_resolved,
                parent_settlement_observation_sha256=parent_observation_sha256,
                parent_settled_at=parent_settled_at,
            )
        if missing_fee_net_field:
            conn.execute(
                "ALTER TABLE paper_trade_accounting RENAME COLUMN fee_net_pnl_dollars TO missing_fee_net_pnl_dollars"
            )

    binding = active_cohort_binding_for_db(runtime.db_path, cohort_id=COHORT_ID)
    assert binding is not None
    receipt_path = repo_root / "logs" / "state" / "runtime_paper_cohort_attestation.json"
    write_runtime_paper_cohort_attestation(
        build_runtime_paper_cohort_attestation(
            runtime,
            cohort_kind="active",
            binding=binding,
            pid=12345,
            started_utc=MATERIALIZED_AT,
        ),
        receipt_path,
    )
    return {
        "repo_root": repo_root,
        "runtime_db": runtime.db_path,
        "receipt": receipt_path,
        "registry": _write_registry(repo_root),
        "regimes": _write_regimes_doc(repo_root),
    }


def _materialize(paths: dict[str, Path], **overrides):
    kwargs = {
        "repo_root": paths["repo_root"],
        "registration_id": REGISTRATION_ID,
        "runtime_attestation_path": paths["receipt"],
        "registry_path": paths["registry"],
        "regimes_doc_path": paths["regimes"],
        "materialized_at_utc": MATERIALIZED_AT,
        "registration_attestor": _fake_registration_attestor,
    }
    kwargs.update(overrides)
    return materialize_runtime_corpus.materialize_runtime_corpus(**kwargs)


def test_materializes_only_attested_active_snapshot_with_fee_net_ledger(
    tmp_path: Path,
) -> None:
    paths = _runtime_fixture(tmp_path)
    runtime_before = paths["runtime_db"].read_bytes()

    result = _materialize(paths)

    assert result.runtime_cohort_id == COHORT_ID
    assert result.runtime_cohort_kind == "active"
    assert result.snapshot_creation_method == "sqlite_online_backup"
    snapshot = paths["repo_root"] / "data" / result.snapshot_path_relative_to_data
    assert snapshot.parent == paths["repo_root"] / "data" / "edge_replay_snapshots" / COHORT_ID
    assert result.snapshot_sha256 == _snapshot_sha256(snapshot)
    assert not os.path.samefile(snapshot, paths["runtime_db"])
    assert not os.lstat(snapshot).st_mode & 0o222
    assert result.runtime_attestation_sha256 == _snapshot_sha256(paths["receipt"])
    assert result.registration_id == REGISTRATION_ID
    assert result.protected_registration_trusted_ref == "origin/main"
    assert result.protected_registration_commit == "a" * 40
    assert result.protected_registration_integrated_at_utc == "2026-07-02T00:00:00Z"
    assert result.to_payload(repo_root=paths["repo_root"])["materialization_status"] == "materialized"
    assert result.output_path.parent == paths["repo_root"] / "logs" / "edge_replay"
    assert result.output_path.name.startswith("corpus_")
    assert result.output_path.suffix == ".jsonl"
    rows = [json.loads(line) for line in result.output_path.read_text().splitlines()]
    assert {row["trade_id"] for row in rows} == {"trade-alpha", "trade-beta"}
    for row in rows:
        assert row["evidence_class"] == "registered_oos"
        assert row["oos_registration_id"] == REGISTRATION_ID
        assert row["fee_net_pnl_dollars"] is not None
        assert row["net_entry_debit_dollars"] is not None
        assert row["net_settlement_payout_dollars"] is not None
        assert row["fee_net_ledger_settled_at"] == MATERIALIZED_AT.isoformat()
        assert row["runtime_attestation_sha256"] == result.runtime_attestation_sha256
        assert row["snapshot_sha256"] == result.snapshot_sha256
        assert row["snapshot_creation_method"] == "sqlite_online_backup"
        assert row["protected_registration_trusted_ref"] == "origin/main"
        assert row["protected_registration_commit"] == "a" * 40
        assert row["protected_registration_integrated_at_utc"] == "2026-07-02T00:00:00Z"
    assert paths["runtime_db"].read_bytes() == runtime_before


def test_public_interface_never_accepts_snapshot_or_database_paths() -> None:
    parameters = inspect.signature(materialize_runtime_corpus.materialize_runtime_corpus).parameters
    assert "snapshot_path" not in parameters
    assert "snapshot_sha256" not in parameters
    assert not any("database" in name or name.endswith("_db") for name in parameters)

    parser = materialize_runtime_corpus._build_argparser()
    assert parser.parse_args(["--registration-id", REGISTRATION_ID]).registration_id == REGISTRATION_ID
    with pytest.raises(SystemExit):
        parser.parse_args(["--registration-id", REGISTRATION_ID, "--snapshot", "untrusted.sqlite"])


def test_validates_owned_snapshot_staging_before_atomic_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _runtime_fixture(tmp_path)
    original_validate = materialize_runtime_corpus._validate_snapshot_contract
    staged_paths: list[Path] = []

    def validate_staging(snapshot: Path, **kwargs) -> None:
        staged_paths.append(snapshot)
        assert snapshot.name.startswith(".snapshot_")
        assert not list(snapshot.parent.glob("snapshot_*.sqlite"))
        original_validate(snapshot, **kwargs)

    monkeypatch.setattr(materialize_runtime_corpus, "_validate_snapshot_contract", validate_staging)
    result = _materialize(paths)

    assert len(staged_paths) == 1
    assert (paths["repo_root"] / "data" / result.snapshot_path_relative_to_data).is_file()


def test_post_build_failure_leaves_no_unbound_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _runtime_fixture(tmp_path)

    def fail_stamp(*_args, **_kwargs) -> None:
        raise materialize_runtime_corpus.RuntimeCorpusMaterializationError("forced provenance failure")

    monkeypatch.setattr(materialize_runtime_corpus, "_stamp_materialization_provenance", fail_stamp)
    with pytest.raises(materialize_runtime_corpus.RuntimeCorpusMaterializationError, match="forced provenance"):
        _materialize(paths)

    output_dir = paths["repo_root"] / "logs" / "edge_replay"
    assert list(output_dir.glob("*")) == []
    snapshot_dir = paths["repo_root"] / "data" / "edge_replay_snapshots" / COHORT_ID
    assert list(snapshot_dir.glob("*")) == []


def test_rejects_pending_runtime_receipt(tmp_path: Path) -> None:
    paths = _runtime_fixture(tmp_path)
    payload = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    payload["cohort_kind"] = "legacy_pending"
    paths["receipt"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(materialize_runtime_corpus.RuntimeCorpusMaterializationError, match="active"):
        _materialize(paths)


@pytest.mark.parametrize(
    ("relative_path", "message"),
    [
        ("../outside.sqlite", "unverified"),
        ("paper_trades.db", "canonical active cohort path"),
    ],
)
def test_rejects_attestation_path_escape_and_root_fallback(
    tmp_path: Path,
    relative_path: str,
    message: str,
) -> None:
    paths = _runtime_fixture(tmp_path)
    payload = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    payload["db_path_relative_to_storage_root"] = relative_path
    paths["receipt"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(materialize_runtime_corpus.RuntimeCorpusMaterializationError, match=message):
        _materialize(paths)


def test_rejects_symlinked_owned_snapshot_root_and_out_of_root_output(tmp_path: Path) -> None:
    paths = _runtime_fixture(tmp_path)
    snapshot_root = paths["repo_root"] / "data" / "edge_replay_snapshots"
    outside = tmp_path / "outside-snapshots"
    outside.mkdir()
    try:
        snapshot_root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(materialize_runtime_corpus.RuntimeCorpusMaterializationError, match="symlink"):
        _materialize(paths)

    paths = _runtime_fixture(tmp_path / "second")
    with pytest.raises(materialize_runtime_corpus.RuntimeCorpusMaterializationError, match="top-level"):
        _materialize(
            paths,
            output_path=paths["repo_root"] / "logs" / "edge_replay" / "nested" / "corpus_bad.jsonl",
        )


def test_rejects_snapshot_missing_registered_family_before_writing_output(tmp_path: Path) -> None:
    paths = _runtime_fixture(tmp_path, settled_trades=(("trade-alpha", "KXALPHA"),))

    with pytest.raises(
        materialize_runtime_corpus.RuntimeCorpusMaterializationError, match="exact registered OOS family"
    ):
        _materialize(paths)

    assert not (paths["repo_root"] / "logs" / "edge_replay").exists()


def test_rejects_snapshot_with_zero_materializable_registered_rows(tmp_path: Path) -> None:
    paths = _runtime_fixture(tmp_path, settled_trades=())

    with pytest.raises(materialize_runtime_corpus.RuntimeCorpusMaterializationError, match="zero materializable"):
        _materialize(paths)

    assert not (paths["repo_root"] / "logs" / "edge_replay").exists()


@pytest.mark.parametrize(
    ("parent_overrides", "message"),
    [
        ({"trade-alpha": {"resolved": 0}}, "not resolved"),
        ({"trade-alpha": {"identity_status": "quarantined"}}, "identity is not mapped"),
        ({"trade-alpha": {"fee_net_accounting_version": 999}}, "accounting version"),
        ({"trade-alpha": {"settlement_observation_sha256": "f" * 64}}, "settlement observation"),
        ({"trade-alpha": {"settled_at": MATERIALIZED_AT + timedelta(seconds=1)}}, "settled_at"),
    ],
)
def test_rejects_parent_fee_net_settlement_link_mismatch(
    tmp_path: Path,
    parent_overrides: dict[str, dict[str, object]],
    message: str,
) -> None:
    paths = _runtime_fixture(tmp_path, parent_overrides=parent_overrides)

    with pytest.raises(materialize_runtime_corpus.RuntimeCorpusMaterializationError, match=message):
        _materialize(paths)

    assert not (paths["repo_root"] / "logs" / "edge_replay").exists()


def test_rejects_open_registration_and_missing_fee_net_replay_field(tmp_path: Path) -> None:
    paths = _runtime_fixture(tmp_path)
    paths["registry"] = _write_registry(
        paths["repo_root"],
        end_utc=datetime(2099, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(materialize_runtime_corpus.RuntimeCorpusMaterializationError, match="closed"):
        _materialize(paths)

    paths = _runtime_fixture(tmp_path / "second", missing_fee_net_field=True)
    with pytest.raises(materialize_runtime_corpus.RuntimeCorpusMaterializationError, match="fee-net replay"):
        _materialize(paths)
