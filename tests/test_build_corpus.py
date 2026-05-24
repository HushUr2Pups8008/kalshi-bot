"""Tests for the I-1 general corpus builder.

Per `docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md`
§3 I-1 — addresses Codex blocker C (calendar-only diversity is not OOS).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.edge_replay import build_corpus


REGIMES_DOC_BODY = """# Pre-registered Corpus Regime Labels

Per framework v3 §3 I-1: regime labels MUST be declared here BEFORE a
corpus is built.

## Active regimes

| Label | Description | First declared |
|---|---|---|
| `pre_p0` | Bot runs before the P0 price-fix sentinel | 2026-05-23 |
| `post_p0_hotfix` | Bot runs after v0.30.1 hotfix landed | 2026-05-23 |
| `post_v030_2_oos_seed` | Bot runs after v0.30.2 release | 2026-05-23 |
"""


def _make_paper_trades_db(path: Path) -> None:
    """Build a minimal paper_trades schema mirror with diverse families."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id     TEXT PRIMARY KEY,
                ts           TEXT NOT NULL,
                ticker       TEXT NOT NULL,
                side         TEXT NOT NULL,
                price_cents  INTEGER NOT NULL,
                series_ticker TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("t1", "2026-05-20T00:00:00Z", "KXTRUMPIRAN-26", "yes", 50, "KXTRUMPIRAN"),
                ("t2", "2026-05-23T21:21:00Z", "KXMOCTRUMP25-1", "no", 40, "KXMOCTRUMP25"),
                ("t3", "2026-05-23T21:22:16Z", "KXTRUMPIRAN-27", "yes", 55, "KXTRUMPIRAN"),
                ("t4", "2026-05-24T00:00:00Z", "KXMOCTRUMP25-2", "no", 45, "KXMOCTRUMP25"),
                ("t5", "2026-05-25T12:00:00Z", "KXOTHER-1", "yes", 60, "KXOTHER"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _write_regimes_doc(repo_root: Path) -> Path:
    doc_dir = repo_root / "docs" / "governance"
    doc_dir.mkdir(parents=True, exist_ok=True)
    doc_path = doc_dir / "corpus-regimes.md"
    doc_path.write_text(REGIMES_DOC_BODY, encoding="utf-8")
    return doc_path


def _start_end() -> tuple[datetime, datetime]:
    return (
        datetime(2026, 5, 23, 21, 22, 16, tzinfo=timezone.utc),
        datetime(2026, 5, 26, 0, 0, 0, tzinfo=timezone.utc),
    )


def test_unregistered_regime_raises(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    start, end = _start_end()
    with pytest.raises(build_corpus.UnregisteredRegimeError):
        build_corpus.build_corpus(
            start_utc=start,
            end_utc=end,
            market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
            cohort_tag="POST_V030_2_OOS_SEED",
            regime_label="NOT_IN_DOC",
            output_path=tmp_path / "out.jsonl",
            paper_trades_db=db,
            regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
        )


def test_required_fields_present_on_every_row(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    start, end = _start_end()
    out = tmp_path / "out.jsonl"
    build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=out,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
    )

    required = {
        "corpus_window_start_utc",
        "corpus_window_end_utc",
        "cohort_tag",
        "regime_label",
        "market_families",
        "corpus_builder_version",
        "built_at_utc",
    }
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) > 0
    for row in rows:
        missing = required - set(row.keys())
        assert not missing, f"row missing fields: {missing}"


def test_row_count_matches_window_and_families(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    start, end = _start_end()
    out = tmp_path / "out.jsonl"
    result = build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=out,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
    )
    # In-window: t3 (TRUMPIRAN), t4 (MOCTRUMP25), t5 (OTHER — excluded by family).
    # Pre-window: t1, t2 — excluded.
    assert result.row_count == 2
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    trade_ids = sorted(r["trade_id"] for r in rows)
    assert trade_ids == ["t3", "t4"]


def test_ts_filter_inclusive_start_exclusive_end(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    # Window touches t3 (== start) but ends exactly at t4 — t4 must be excluded.
    start = datetime(2026, 5, 23, 21, 22, 16, tzinfo=timezone.utc)
    end = datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
    out = tmp_path / "out.jsonl"
    result = build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=out,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
    )
    assert result.row_count == 1
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows[0]["trade_id"] == "t3"


def test_empty_window_yields_no_rows(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    start = datetime(2027, 1, 1, tzinfo=timezone.utc)
    end = datetime(2027, 2, 1, tzinfo=timezone.utc)
    out = tmp_path / "out.jsonl"
    result = build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=out,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
    )
    assert result.row_count == 0
    assert out.exists()
    assert out.read_text() == ""


def test_in_period_validation_only_true_for_single_family(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    start, end = _start_end()
    out = tmp_path / "out.jsonl"
    result = build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN"],  # only one family
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=out,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
    )
    assert result.in_period_validation_only is True
    assert any("IN_PERIOD" in n or "in_period" in n for n in result.notes)


def test_in_period_validation_only_false_when_two_or_more_families(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    start, end = _start_end()
    out = tmp_path / "out.jsonl"
    result = build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=out,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
    )
    assert result.in_period_validation_only is False


def test_pragma_query_only_db_unchanged(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    pre_mtime = db.stat().st_mtime
    with sqlite3.connect(db) as _pre_conn:
        pre_count = _pre_conn.execute(
            "SELECT COUNT(*) FROM paper_trades"
        ).fetchone()[0]

    start, end = _start_end()
    out = tmp_path / "out.jsonl"
    build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=out,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
    )

    post_mtime = db.stat().st_mtime
    with sqlite3.connect(db) as _post_conn:
        post_count = _post_conn.execute(
            "SELECT COUNT(*) FROM paper_trades"
        ).fetchone()[0]
    assert pre_mtime == post_mtime
    assert pre_count == post_count


def test_idempotent_overwrite(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    start, end = _start_end()
    out = tmp_path / "out.jsonl"

    # Pin built_at_utc so two runs are byte-equal.
    fixed_now = "2026-05-24T00:00:00Z"
    build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=out,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
        built_at_utc=fixed_now,
    )
    first = out.read_text()

    build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=out,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
        built_at_utc=fixed_now,
    )
    second = out.read_text()
    assert first == second


def test_default_output_path_when_none(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)
    # Run within tmp_path so the default relative path lands in a sandbox.
    monkeypatch.chdir(tmp_path)

    start, end = _start_end()
    result = build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=None,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
    )
    expected_name_parts = [
        "corpus_post_v030_2_oos_seed",
        "POST_V030_2_OOS_SEED",
        "2026-05-23T21-22-16Z",
        "2026-05-26T00-00-00Z",
    ]
    name = Path(result.output_path).name
    for part in expected_name_parts:
        assert part in name, f"expected {part!r} in default output name {name!r}"


def test_required_field_values_correct(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    start, end = _start_end()
    out = tmp_path / "out.jsonl"
    build_corpus.build_corpus(
        start_utc=start,
        end_utc=end,
        market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
        cohort_tag="POST_V030_2_OOS_SEED",
        regime_label="post_v030_2_oos_seed",
        output_path=out,
        paper_trades_db=db,
        regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
        built_at_utc="2026-05-24T00:00:00Z",
    )

    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows, "expected non-empty corpus"
    for row in rows:
        assert row["corpus_window_start_utc"] == "2026-05-23T21:22:16Z"
        assert row["corpus_window_end_utc"] == "2026-05-26T00:00:00Z"
        assert row["cohort_tag"] == "POST_V030_2_OOS_SEED"
        assert row["regime_label"] == "post_v030_2_oos_seed"
        assert sorted(row["market_families"]) == ["KXMOCTRUMP25", "KXTRUMPIRAN"]
        assert row["corpus_builder_version"] == build_corpus.CORPUS_BUILDER_VERSION
        assert row["built_at_utc"] == "2026-05-24T00:00:00Z"


def test_missing_db_cli_exit_code_2(tmp_path: Path) -> None:
    _write_regimes_doc(tmp_path)
    # CLI runs with no DB.
    exit_code = build_corpus.main(
        [
            "--start-ts",
            "2026-05-23T21:22:16Z",
            "--end-ts",
            "2026-05-26T00:00:00Z",
            "--market-families",
            "KXTRUMPIRAN,KXMOCTRUMP25",
            "--cohort-tag",
            "POST_V030_2_OOS_SEED",
            "--regime-label",
            "post_v030_2_oos_seed",
            "--db",
            str(tmp_path / "no_such.db"),
            "--output",
            str(tmp_path / "out.jsonl"),
            "--regimes-doc",
            str(tmp_path / "docs" / "governance" / "corpus-regimes.md"),
        ]
    )
    assert exit_code == 2


def test_regimes_doc_parser_robust_to_whitespace_and_comments(tmp_path: Path) -> None:
    doc = tmp_path / "regimes.md"
    doc.write_text(
        """# Header line

<!-- comment-like noise -->

## Active regimes

| Label | Description | First declared |
|---|---|---|
|   `pre_p0`   | Pre-P0 | 2026-05-23 |

|`post_p0_hotfix`|hotfix|2026-05-23|

   | `post_v030_2_oos_seed` | seed window | 2026-05-23 |


Stuff that is not a table row should be ignored.
""",
        encoding="utf-8",
    )
    labels = build_corpus.load_registered_regimes(doc)
    assert labels == {"pre_p0", "post_p0_hotfix", "post_v030_2_oos_seed"}


def test_unregistered_regime_error_message_is_actionable(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    start, end = _start_end()
    try:
        build_corpus.build_corpus(
            start_utc=start,
            end_utc=end,
            market_families=["KXTRUMPIRAN", "KXMOCTRUMP25"],
            cohort_tag="POST_V030_2_OOS_SEED",
            regime_label="brand_new_regime",
            output_path=tmp_path / "out.jsonl",
            paper_trades_db=db,
            regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
        )
    except build_corpus.UnregisteredRegimeError as exc:
        msg = str(exc)
        assert "brand_new_regime" in msg
        assert "corpus-regimes.md" in msg
    else:  # pragma: no cover
        raise AssertionError("expected UnregisteredRegimeError")


def test_inverted_window_bounds_raise_value_error(tmp_path: Path) -> None:
    """Inverted start/end silently match zero rows under the SQL filter,
    producing a misleading empty corpus with in_period_validation_only=True.
    Per python-reviewer MEDIUM: fail fast on inverted bounds so the
    operator notices the typo rather than acts on a falsely-quiet window."""
    db = tmp_path / "paper_trades.db"
    _make_paper_trades_db(db)
    _write_regimes_doc(tmp_path)

    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 5, 1, tzinfo=timezone.utc)  # before start!

    try:
        build_corpus.build_corpus(
            start_utc=start,
            end_utc=end,
            market_families=["KXTRUMPIRAN"],
            cohort_tag="POST_V030_2_OOS_SEED",
            regime_label="post_v030_2_oos_seed",
            output_path=tmp_path / "out.jsonl",
            paper_trades_db=db,
            regimes_doc_path=tmp_path / "docs" / "governance" / "corpus-regimes.md",
        )
    except ValueError as exc:
        msg = str(exc)
        assert "start_utc must be strictly before end_utc" in msg
    else:  # pragma: no cover
        raise AssertionError("expected ValueError on inverted bounds")
