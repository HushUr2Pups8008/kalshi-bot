from __future__ import annotations

import json
import sqlite3

import scripts.polymarket_feedback_state_audit as pm_audit
from scripts.polymarket_feedback_state_audit import (
    apply_quarantine_plan,
    audit_feedback_state,
    build_quarantine_plan,
    main,
    render_audit,
)


def _seed_counters(path):
    conn = sqlite3.connect(str(path))
    with conn:
        conn.execute(
            """CREATE TABLE match_token_fp_counters (
                token TEXT NOT NULL,
                market_prefix TEXT NOT NULL,
                day_utc TEXT NOT NULL,
                fp_neutral_count INTEGER NOT NULL DEFAULT 0,
                true_positive_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (token, market_prefix, day_utc)
            )"""
        )
        conn.executemany(
            "INSERT INTO match_token_fp_counters VALUES (?, ?, ?, ?, ?)",
            [
                ("trump", "polymarket_us", "2026-06-10", 3, 1),
                ("iran", "polymarket_us", "2026-06-11", 2, 0),
                ("dem", "polymarket_us:ewc-usse-me", "2026-06-10", 1, 4),
                ("gop", "polymarket_us:ewc-usse-me", "2026-06-11", 0, 2),
                ("rate", "KXFED", "2026-06-11", 5, 1),
            ],
        )
    conn.close()


def test_audit_counts_bare_and_family_polymarket_state(tmp_path):
    counters = tmp_path / "match_token_fp_counters.db"
    weights = tmp_path / "matcher_token_weights.json"
    _seed_counters(counters)
    weights.write_text(
        json.dumps(
            {
                "polymarket_us:trump": {"weight": 0.1},
                "polymarket_us:iran": {"weight": 0.2},
                "polymarket_us:ewc-usse-me:dem": {"weight": 0.4},
                "KXFED:rate": {"weight": 0.9},
            }
        ),
        encoding="utf-8",
    )

    audit = audit_feedback_state(counters_path=counters, weights_path=weights)

    assert audit.counters.bare_pm_rows == 2
    assert audit.counters.family_pm_rows == 2
    assert audit.counters.other_rows == 1
    assert audit.counters.bare_pm_observations == 6
    assert audit.weights.bare_pm_keys == 2
    assert audit.weights.family_pm_keys == 1
    assert audit.weights.other_keys == 1


def test_quarantine_plan_is_review_only_and_targets_only_bare_pm_state(tmp_path):
    counters = tmp_path / "match_token_fp_counters.db"
    _seed_counters(counters)

    plan = build_quarantine_plan(counters_path=counters, weights_path=tmp_path / "weights.json")

    assert plan.write_required is False
    assert "market_prefix = 'polymarket_us'" in plan.counter_sql
    assert "market_prefix GLOB 'polymarket_us:*'" not in plan.counter_sql
    assert "remove JSON keys matching bare runtime pattern polymarket_us:<token>" in plan.weight_operation

    conn = sqlite3.connect(str(counters))
    try:
        assert conn.execute("SELECT COUNT(*) FROM match_token_fp_counters").fetchone()[0] == 5
        assert conn.execute(
            "SELECT COUNT(*) FROM match_token_fp_counters WHERE market_prefix = 'polymarket_us'"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_render_audit_explains_read_only_counts_and_plan(tmp_path):
    counters = tmp_path / "match_token_fp_counters.db"
    weights = tmp_path / "matcher_token_weights.json"
    _seed_counters(counters)
    weights.write_text(json.dumps({"polymarket_us:trump": {"weight": 0.1}}), encoding="utf-8")

    text = render_audit(audit_feedback_state(counters_path=counters, weights_path=weights))

    assert "mode: read-only" in text
    assert "bare DB rows market_prefix='polymarket_us': 2" in text
    assert "family DB rows market_prefix GLOB 'polymarket_us:*': 2" in text
    assert "bare runtime weight keys polymarket_us:<token>: 1" in text
    assert "review-only quarantine plan" in text
    assert "DELETE FROM match_token_fp_counters" in text


def test_cli_defaults_to_read_only_and_accepts_fixture_paths(tmp_path, capsys):
    counters = tmp_path / "match_token_fp_counters.db"
    weights = tmp_path / "matcher_token_weights.json"
    _seed_counters(counters)
    weights.write_text(json.dumps({"polymarket_us:trump": {"weight": 0.1}}), encoding="utf-8")

    code = main(["--counters", str(counters), "--weights", str(weights)])

    out = capsys.readouterr().out
    assert code == 0
    assert "mode: read-only" in out
    assert "writes executed: no" in out
    assert counters.exists()


def test_audit_counters_does_not_create_sqlite_sidecars_for_wal_db(tmp_path):
    counters = tmp_path / "match_token_fp_counters.db"
    weights = tmp_path / "matcher_token_weights.json"
    _seed_counters(counters)
    weights.write_text("{}", encoding="utf-8")

    conn = sqlite3.connect(str(counters))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        counters.with_name(counters.name + suffix).unlink(missing_ok=True)

    audit_feedback_state(counters_path=counters, weights_path=weights)

    assert not counters.with_name(counters.name + "-wal").exists()
    assert not counters.with_name(counters.name + "-shm").exists()


def test_audit_counters_opens_sqlite_database_in_uri_read_only_mode(tmp_path, monkeypatch):
    counters = tmp_path / "match_token_fp_counters.db"
    weights = tmp_path / "matcher_token_weights.json"
    _seed_counters(counters)
    weights.write_text("{}", encoding="utf-8")
    calls = []
    real_connect = sqlite3.connect

    def spy_connect(database, *args, **kwargs):
        calls.append((database, kwargs))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(pm_audit.sqlite3, "connect", spy_connect)

    audit_feedback_state(counters_path=counters, weights_path=weights)

    assert calls
    database, kwargs = calls[0]
    assert database.startswith("file:")
    assert "mode=ro" in database
    assert "immutable=1" in database
    assert kwargs.get("uri") is True


def test_apply_quarantine_plan_removes_only_bare_pm_state_with_backup(tmp_path):
    counters = tmp_path / "match_token_fp_counters.db"
    weights = tmp_path / "matcher_token_weights.json"
    backup_dir = tmp_path / "backup"
    _seed_counters(counters)
    weights.write_text(
        json.dumps(
            {
                "polymarket_us": {"weight": 0.3},
                "polymarket_us:trump": {"weight": 0.1},
                "polymarket_us:ewc-usse-me:dem": {"weight": 0.4},
                "KXFED:rate": {"weight": 0.9},
            }
        ),
        encoding="utf-8",
    )

    summary = apply_quarantine_plan(counters_path=counters, weights_path=weights, backup_dir=backup_dir)

    assert summary["counters"]["bare_pm_removed"] == 2
    assert summary["weights"]["bare_pm_removed"] == 2
    assert summary["verify"]["bare_pm_counter_rows_remaining"] == 0
    assert summary["verify"]["bare_pm_weight_keys_remaining"] == 0
    assert summary["verify"]["family_pm_counter_rows_remaining"] == 2
    assert summary["verify"]["family_pm_weight_keys_remaining"] == 1
    assert (backup_dir / counters.name).exists()
    assert (backup_dir / weights.name).exists()

    remaining_weights = json.loads(weights.read_text(encoding="utf-8"))
    assert sorted(remaining_weights) == ["KXFED:rate", "polymarket_us:ewc-usse-me:dem"]

    conn = sqlite3.connect(str(counters))
    try:
        assert conn.execute("SELECT COUNT(*) FROM match_token_fp_counters").fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM match_token_fp_counters WHERE market_prefix GLOB 'polymarket_us:*'"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_cli_requires_explicit_apply_confirmation_for_quarantine(tmp_path, capsys):
    counters = tmp_path / "match_token_fp_counters.db"
    weights = tmp_path / "matcher_token_weights.json"
    _seed_counters(counters)
    weights.write_text(json.dumps({"polymarket_us:trump": {"weight": 0.1}}), encoding="utf-8")

    code = main(["--counters", str(counters), "--weights", str(weights), "--apply-quarantine"])

    out = capsys.readouterr().out
    assert code == 2
    assert "--confirm-runtime-mutation" in out
    assert json.loads(weights.read_text(encoding="utf-8")) == {"polymarket_us:trump": {"weight": 0.1}}
