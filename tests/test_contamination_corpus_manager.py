"""Tests for the I-10 contamination-window manager.

Per ``docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md``
§3 I-10 — closes Codex blocker E (silently excluding observation-window
rows from the corpus creates the sampling bias the gate machinery was
meant to prevent). The exporter is the apples-to-apples retention path.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.edge_replay import contamination_corpus_manager as ccm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sentinel(tmp_path: Path) -> Path:
    return tmp_path / "active_contamination_window.json"


def _make_paper_trades_db(
    path: Path,
    *,
    include_cohort_column: bool = True,
) -> None:
    """Build a minimal paper_trades schema for export tests."""
    conn = sqlite3.connect(path)
    try:
        cohort_col = ",\n            cohort_extension TEXT" if include_cohort_column else ""
        conn.execute(
            f"""
            CREATE TABLE paper_trades (
                trade_id     TEXT PRIMARY KEY,
                ts           TEXT NOT NULL,
                ticker       TEXT NOT NULL,
                side         TEXT NOT NULL,
                price_cents  INTEGER NOT NULL{cohort_col}
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_trade(
    db: Path,
    trade_id: str,
    ts: str,
    *,
    cohort_extension: str | None = None,
) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO paper_trades "
            "(trade_id, ts, ticker, side, price_cents, cohort_extension) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (trade_id, ts, "KX-DEMO-1", "yes", 50, cohort_extension),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_active_window
# ---------------------------------------------------------------------------


def test_get_active_window_returns_none_when_sentinel_absent(tmp_path: Path) -> None:
    assert ccm.get_active_window(sentinel_path=_sentinel(tmp_path)) is None


def test_get_active_window_returns_window_when_sentinel_present(tmp_path: Path) -> None:
    sentinel = _sentinel(tmp_path)
    sentinel.write_text(
        json.dumps(
            {
                "change_id": "EXEC-002-v2",
                "window_id": "20260524T010000Z",
                "opened_at_utc": "2026-05-24T01:00:00Z",
                "closes_at_utc": "2026-05-27T01:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    window = ccm.get_active_window(sentinel_path=sentinel)

    assert window is not None
    assert window.change_id == "EXEC-002-v2"
    assert window.window_id == "20260524T010000Z"
    assert window.opened_at_utc == "2026-05-24T01:00:00Z"
    assert window.closes_at_utc == "2026-05-27T01:00:00Z"


def test_get_active_window_returns_none_on_malformed_json(tmp_path: Path) -> None:
    sentinel = _sentinel(tmp_path)
    sentinel.write_text("{ not valid json", encoding="utf-8")

    assert ccm.get_active_window(sentinel_path=sentinel) is None


def test_get_active_window_returns_none_on_missing_keys(tmp_path: Path) -> None:
    sentinel = _sentinel(tmp_path)
    sentinel.write_text(json.dumps({"change_id": "X"}), encoding="utf-8")

    # Missing required keys must not raise — fail-soft contract for the
    # write-path caller in trading.paper_trader.
    assert ccm.get_active_window(sentinel_path=sentinel) is None


def test_get_active_window_returns_none_when_payload_not_object(tmp_path: Path) -> None:
    sentinel = _sentinel(tmp_path)
    sentinel.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    assert ccm.get_active_window(sentinel_path=sentinel) is None


# ---------------------------------------------------------------------------
# open_window
# ---------------------------------------------------------------------------


def test_open_window_writes_sentinel_and_returns_window(tmp_path: Path) -> None:
    sentinel = _sentinel(tmp_path)
    now = datetime(2026, 5, 24, 1, 0, 0, tzinfo=timezone.utc)

    window = ccm.open_window(
        "EXEC-002-v2",
        duration_seconds=3 * 24 * 3600,
        sentinel_path=sentinel,
        now_utc=now,
    )

    assert window.change_id == "EXEC-002-v2"
    assert window.window_id == "20260524T010000Z"
    assert window.opened_at_utc == "2026-05-24T01:00:00Z"
    assert window.closes_at_utc == "2026-05-27T01:00:00Z"
    assert sentinel.exists()
    payload = json.loads(sentinel.read_text(encoding="utf-8"))
    assert payload["change_id"] == "EXEC-002-v2"
    assert payload["window_id"] == "20260524T010000Z"


def test_open_window_raises_when_sentinel_already_exists(tmp_path: Path) -> None:
    sentinel = _sentinel(tmp_path)
    ccm.open_window("FIRST", duration_seconds=60, sentinel_path=sentinel)

    with pytest.raises(ccm.ContaminationWindowAlreadyOpenError):
        ccm.open_window("SECOND", duration_seconds=60, sentinel_path=sentinel)


def test_open_window_rejects_empty_change_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ccm.open_window("   ", duration_seconds=60, sentinel_path=_sentinel(tmp_path))


def test_open_window_rejects_nonpositive_duration(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ccm.open_window("X", duration_seconds=0, sentinel_path=_sentinel(tmp_path))


def test_open_window_writes_atomically(tmp_path: Path) -> None:
    """The visible sentinel must never be a partial write — Path.replace
    from a .tmp file is the contract. We can only check that no .tmp
    file is left behind after a successful open.
    """
    sentinel = _sentinel(tmp_path)
    ccm.open_window("X", duration_seconds=60, sentinel_path=sentinel)

    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


# ---------------------------------------------------------------------------
# close_active_window
# ---------------------------------------------------------------------------


def test_close_active_window_deletes_sentinel_and_returns_window(tmp_path: Path) -> None:
    sentinel = _sentinel(tmp_path)
    opened = ccm.open_window("X", duration_seconds=60, sentinel_path=sentinel)

    closed = ccm.close_active_window(sentinel_path=sentinel)

    assert closed is not None
    assert closed.change_id == opened.change_id
    assert closed.window_id == opened.window_id
    assert not sentinel.exists()


def test_close_active_window_returns_none_when_sentinel_absent(tmp_path: Path) -> None:
    assert ccm.close_active_window(sentinel_path=_sentinel(tmp_path)) is None


def test_close_active_window_removes_corrupt_sentinel(tmp_path: Path) -> None:
    sentinel = _sentinel(tmp_path)
    sentinel.write_text("garbage", encoding="utf-8")

    result = ccm.close_active_window(sentinel_path=sentinel)

    assert result is None  # malformed payload returns None ...
    assert not sentinel.exists()  # ... but the file is still removed


# ---------------------------------------------------------------------------
# cohort_extension_tag
# ---------------------------------------------------------------------------


def test_cohort_extension_tag_format() -> None:
    window = ccm.ContaminationWindow(
        change_id="EXEC-002-v2",
        window_id="20260524T010000Z",
        opened_at_utc="2026-05-24T01:00:00Z",
        closes_at_utc="2026-05-27T01:00:00Z",
    )

    assert (
        ccm.cohort_extension_tag(window)
        == "contamination_window:EXEC-002-v2:20260524T010000Z"
    )


# ---------------------------------------------------------------------------
# export_contamination_corpus
# ---------------------------------------------------------------------------


def test_export_contamination_corpus_filters_by_change_id(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _insert_trade(db, "t1", "2026-05-24T01:00:00Z",
                  cohort_extension="contamination_window:EXEC-002-v2:20260524T010000Z")
    _insert_trade(db, "t2", "2026-05-24T02:00:00Z",
                  cohort_extension="contamination_window:OTHER:20260524T020000Z")
    _insert_trade(db, "t3", "2026-05-24T03:00:00Z", cohort_extension=None)

    output = ccm.export_contamination_corpus(
        "EXEC-002-v2",
        paper_trades_db=db,
        output_path=tmp_path / "exec002.jsonl",
    )

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["trade_id"] == "t1"
    assert row["contamination_window_id"] == "20260524T010000Z"


def test_export_contamination_corpus_handles_zero_matches(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _insert_trade(db, "t1", "2026-05-24T01:00:00Z", cohort_extension=None)

    output = ccm.export_contamination_corpus(
        "EXEC-002-v2",
        paper_trades_db=db,
        output_path=tmp_path / "exec002.jsonl",
    )

    assert output.exists()
    assert output.read_text(encoding="utf-8") == ""


def test_export_contamination_corpus_safe_when_column_missing(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db, include_cohort_column=False)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO paper_trades (trade_id, ts, ticker, side, price_cents) "
            "VALUES (?, ?, ?, ?, ?)",
            ("t1", "2026-05-24T01:00:00Z", "KX-DEMO-1", "yes", 50),
        )
        conn.commit()
    finally:
        conn.close()

    output = ccm.export_contamination_corpus(
        "EXEC-002-v2",
        paper_trades_db=db,
        output_path=tmp_path / "exec002.jsonl",
    )

    # Pre-migration safety: empty JSONL, no error.
    assert output.exists()
    assert output.read_text(encoding="utf-8") == ""


def test_export_contamination_corpus_raises_on_missing_db(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ccm.export_contamination_corpus(
            "EXEC-002-v2",
            paper_trades_db=tmp_path / "does-not-exist.db",
        )


def test_export_contamination_corpus_rejects_empty_change_id(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    with pytest.raises(ValueError):
        ccm.export_contamination_corpus("   ", paper_trades_db=db)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_status_reports_active_window(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = _sentinel(tmp_path)
    ccm.open_window("EXEC-002-v2", duration_seconds=60, sentinel_path=sentinel)

    rc = ccm.main(["status", "--sentinel", str(sentinel)])
    out = capsys.readouterr().out

    assert rc == 0
    payload = json.loads(out)
    assert payload["active"]["change_id"] == "EXEC-002-v2"
    assert payload["cohort_extension_tag"].startswith(
        "contamination_window:EXEC-002-v2:"
    )


def test_cli_status_reports_no_active_window(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = _sentinel(tmp_path)

    rc = ccm.main(["status", "--sentinel", str(sentinel)])
    out = capsys.readouterr().out

    assert rc == 0
    assert json.loads(out) == {"active": None}


def test_cli_open_close_roundtrip(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = _sentinel(tmp_path)

    rc_open = ccm.main(
        ["open", "--change-id", "EXEC-002-v2", "--duration-seconds", "60",
         "--sentinel", str(sentinel)]
    )
    open_out = capsys.readouterr().out
    assert rc_open == 0
    assert json.loads(open_out)["change_id"] == "EXEC-002-v2"
    assert sentinel.exists()

    rc_close = ccm.main(["close", "--sentinel", str(sentinel)])
    close_out = capsys.readouterr().out
    assert rc_close == 0
    assert json.loads(close_out)["closed"]["change_id"] == "EXEC-002-v2"
    assert not sentinel.exists()


def test_cli_open_rejects_double_open(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = _sentinel(tmp_path)
    ccm.open_window("FIRST", duration_seconds=60, sentinel_path=sentinel)

    rc = ccm.main(
        ["open", "--change-id", "SECOND", "--duration-seconds", "60",
         "--sentinel", str(sentinel)]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "already exists" in err


def test_cli_export_writes_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _insert_trade(db, "t1", "2026-05-24T01:00:00Z",
                  cohort_extension="contamination_window:EXEC-002-v2:20260524T010000Z")

    output = tmp_path / "exec002.jsonl"
    rc = ccm.main(
        ["export", "--change-id", "EXEC-002-v2", "--db", str(db),
         "--output", str(output)]
    )
    out = capsys.readouterr().out

    assert rc == 0
    payload = json.loads(out)
    assert payload["change_id"] == "EXEC-002-v2"
    assert Path(payload["output_path"]) == output
    assert output.exists()
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# Post-review hardening (python-reviewer M1 + security-reviewer #2, #6)
# ---------------------------------------------------------------------------


def test_open_window_rejects_like_wildcard_in_change_id(tmp_path: Path) -> None:
    """``%`` in change_id is a SQL LIKE wildcard. Reject at write-time
    so bad input never lands in the sentinel or cohort_extension column."""
    sentinel = tmp_path / "sentinel.json"
    try:
        ccm.open_window("EXEC%-002", duration_seconds=60, sentinel_path=sentinel)
    except ValueError as exc:
        assert "%" in str(exc) or "forbidden token" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError on LIKE-wildcard change_id")
    assert not sentinel.exists()


def test_open_window_rejects_path_traversal_in_change_id(tmp_path: Path) -> None:
    """``..`` / ``/`` / ``\\`` in change_id would traverse the corpus dir
    when interpolated into the export path. Reject at write-time."""
    sentinel = tmp_path / "sentinel.json"
    for bad in ("../etc/passwd", "abs/path", "back\\slash", ".."):
        try:
            ccm.open_window(bad, duration_seconds=60, sentinel_path=sentinel)
        except ValueError as exc:
            assert "forbidden token" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(
                f"expected ValueError on traversal change_id: {bad!r}"
            )
        assert not sentinel.exists()


def test_open_window_rejects_whitespace_in_change_id(tmp_path: Path) -> None:
    """Whitespace survives JSON round-trips invisibly and produces
    confusing operator-visible labels."""
    sentinel = tmp_path / "sentinel.json"
    for bad in ("with space", "with\ttab", "with\nnewline"):
        try:
            ccm.open_window(bad, duration_seconds=60, sentinel_path=sentinel)
        except ValueError as exc:
            assert "forbidden token" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(
                f"expected ValueError on whitespace change_id: {bad!r}"
            )


def test_open_window_allows_underscore_in_change_id(tmp_path: Path) -> None:
    """``_`` is a SQL LIKE wildcard too, but it is permitted because it
    is extremely common in identifiers and the startswith post-filter
    rescues correctness of the emitted output."""
    sentinel = tmp_path / "sentinel.json"
    window = ccm.open_window(
        "test_window_with_underscore",
        duration_seconds=60,
        sentinel_path=sentinel,
    )
    assert window.change_id == "test_window_with_underscore"
    assert sentinel.exists()


def test_default_export_path_rejects_traversal(
    monkeypatch, tmp_path: Path
) -> None:
    """Security-reviewer item 6: defense-in-depth containment guard on
    the export path. Even if _validate_change_id is bypassed (e.g.,
    export invoked without an open_window first), the export must
    not write outside the corpus directory."""
    corpora_dir = tmp_path / "contamination_corpora"
    corpora_dir.mkdir(parents=True)
    monkeypatch.setattr(ccm, "CONTAMINATION_CORPORA_DIR", corpora_dir)

    try:
        ccm._default_export_path("../../../etc/passwd")
    except ValueError as exc:
        assert "outside the corpus directory" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError on traversal export path")
