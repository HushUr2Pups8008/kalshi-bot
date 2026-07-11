from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tasks.research_dossier import RESEARCH_TASK_INITIAL_BACKOFF_SECONDS

from scripts import research_decision_grade_repair as repair_module
from scripts.research_decision_grade_repair import (
    find_invalid_decision_grade_candidates,
    find_repairable_research_tasks,
    repair_invalid_decision_grade_candidates,
    repair_research_task_blockers,
)


def _write_repair_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE research_runs (
                research_run_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                verdict_status TEXT NOT NULL,
                force_side TEXT,
                estimated_probability REAL,
                confidence REAL,
                market_price REAL,
                estimated_edge REAL,
                created_ts TEXT
            );
            CREATE TABLE research_evidence (
                evidence_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                source_class TEXT NOT NULL,
                source_name TEXT,
                source_url TEXT,
                claim_type TEXT,
                supports_direction TEXT,
                supports_confidence REAL,
                retrieved_at TEXT,
                inserted_at TEXT,
                contract_fingerprint TEXT
            );
            CREATE TABLE research_run_queries (
                research_run_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                query TEXT NOT NULL,
                query_intent TEXT NOT NULL,
                source_class TEXT NOT NULL,
                PRIMARY KEY (research_run_id, ordinal)
            );
            CREATE TABLE research_tasks (
                market_ticker TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                cooldown_until_ts TEXT,
                backoff_seconds REAL NOT NULL DEFAULT 0,
                terminal_reason TEXT,
                open_questions_json TEXT NOT NULL DEFAULT '[]',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                same_reason_count INTEGER NOT NULL DEFAULT 0,
                last_skip_reason TEXT,
                updated_ts TEXT NOT NULL DEFAULT '2026-06-29T10:00:00Z'
            );
            CREATE TABLE research_dossiers (
                market_ticker TEXT PRIMARY KEY,
                last_research_run_id TEXT,
                last_contract_fingerprint TEXT,
                last_researched_ts TEXT NOT NULL,
                last_verdict_status TEXT NOT NULL,
                last_skip_reason TEXT,
                last_force_side TEXT,
                last_estimated_probability REAL,
                last_confidence REAL,
                last_market_price REAL,
                last_estimated_edge REAL,
                last_decision_grade_status TEXT,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        for ticker, run_id, counter_direction in (
            ("KXBAD", "rr-bad", "yes"),
            ("KXGOOD", "rr-good", "no"),
            ("KXSAME", "rr-same", "no"),
            ("KXSTALEDOSSIER", "rr-stale-dossier", "no"),
        ):
            conn.execute(
                """
                INSERT INTO research_runs VALUES (
                    ?, ?, 'decision_grade_candidate', 'yes',
                    0.64, 0.74, 0.51, 0.12, '2026-06-29T10:00:00Z'
                )
                """,
                (run_id, ticker),
            )
            task_state = (
                "needs_research"
                if ticker == "KXSTALEDOSSIER"
                else "decision_grade_candidate"
            )
            last_skip_reason = (
                "no_reliable_source_path"
                if ticker == "KXSTALEDOSSIER"
                else None
            )
            conn.execute(
                """
                INSERT INTO research_tasks (
                    market_ticker, state, cooldown_until_ts, backoff_seconds,
                    terminal_reason, last_skip_reason
                ) VALUES (?, ?, NULL, 0, NULL, ?)
                """,
                (ticker, task_state, last_skip_reason),
            )
            conn.execute(
                """
                INSERT INTO research_dossiers VALUES (
                    ?, ?, 'fp', '2026-06-29T10:00:00Z',
                    'decision_grade_candidate', NULL, 'yes',
                    0.64, 0.74, 0.51, 0.12,
                    'decision_grade_candidate',
                    '2026-06-29T10:00:00Z',
                    '2026-06-29T10:00:00Z'
                )
                """,
                (ticker, run_id),
            )
            query_rows = (
                ("supporting", "reputable_secondary"),
                ("official_resolution", "resolution_source"),
                ("rules", "rules_source"),
                ("market_price", "market_price"),
                ("staleness_check", "official_primary"),
                ("disconfirming", "reputable_secondary"),
            )
            for ordinal, (query_intent, source_class) in enumerate(query_rows):
                conn.execute(
                    """
                    INSERT INTO research_run_queries VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        ordinal,
                        f"{ticker} {query_intent}",
                        query_intent,
                        source_class,
                    ),
                )
            rows = (
                (
                    "resolution_source",
                    "settlement",
                    "yes",
                    "https://agency.gov/final",
                ),
                (
                    "rules_source",
                    "rules",
                    "neutral",
                    "https://kalshi.com/rules/contract",
                ),
                (
                    "official_primary",
                    "disconfirming",
                    counter_direction,
                    "https://apnews.com/report",
                ),
            )
            if ticker in {"KXSAME", "KXSTALEDOSSIER"}:
                rows = tuple(
                    (source_class, claim_type, direction, "https://wire.example.com/story")
                    for source_class, claim_type, direction, _source_url in rows
                )
            for index, (source_class, claim_type, direction, source_url) in enumerate(
                rows,
                start=1,
            ):
                conn.execute(
                    """
                    INSERT INTO research_evidence VALUES (
                        ?, ?, ?, ?, 'source', ?, ?, ?, 0.9,
                        '2026-06-29T10:00:00Z',
                        '2026-06-29T10:00:00Z',
                        'fp'
                    )
                    """,
                    (
                        f"{run_id}-{index}",
                        ticker,
                        run_id,
                        source_class,
                        source_url,
                        claim_type,
                        direction,
                    ),
                )
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, cooldown_until_ts, backoff_seconds,
                terminal_reason, last_skip_reason, same_reason_count
            ) VALUES (
                'KXTIMEOUT',
                'untradeable',
                NULL,
                0,
                'research_timeout_exhausted',
                'research_timeout',
                1
            ), (
                'KXSTALESOURCE',
                'needs_research',
                '2026-06-29T11:00:00Z',
                1200,
                NULL,
                'missing_resolution_source',
                3
            ), (
                'KXNEUTRAL',
                'needs_counter_evidence',
                '2026-06-29T11:00:00Z',
                1200,
                NULL,
                'neutral_only_evidence',
                3
            ), (
                'KXTIMEOUTCHURN',
                'continue_researching',
                NULL,
                0,
                NULL,
                'research_timeout_exhausted',
                3
            ), (
                'KXOFFICIALSLOW',
                'needs_research',
                '2026-07-02T18:00:00Z',
                21600,
                NULL,
                'official_data_pending',
                8
            )
            """
        )


def test_repair_dry_run_reports_invalid_candidates_without_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence_store.db"
    _write_repair_db(db_path)

    candidates = find_invalid_decision_grade_candidates(db_path)
    task_candidates = find_repairable_research_tasks(db_path)

    assert [(candidate.market_ticker, candidate.reason) for candidate in candidates] == [
        ("KXBAD", "missing_counter_evidence"),
        ("KXSAME", "no_reliable_source_path"),
    ]
    assert [
        (
            candidate.market_ticker,
            candidate.reason,
            candidate.next_state,
            candidate.terminal_reason,
        )
        for candidate in task_candidates
    ] == [
        ("KXTIMEOUT", "research_timeout_exhausted", "continue_researching", None),
        (
            "KXSTALESOURCE",
            "missing_resolution_source",
            "untradeable",
            "no_reliable_source_path",
        ),
    ]
    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            "SELECT state FROM research_tasks WHERE market_ticker = 'KXBAD'"
        ).fetchone()[0]
        timeout_state = conn.execute(
            "SELECT state FROM research_tasks WHERE market_ticker = 'KXTIMEOUT'"
        ).fetchone()[0]
    assert state == "decision_grade_candidate"
    assert timeout_state == "untradeable"


def test_repair_apply_requeues_invalid_candidates_only(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence_store.db"
    _write_repair_db(db_path)

    repaired = repair_invalid_decision_grade_candidates(db_path)
    repaired_tasks = repair_research_task_blockers(db_path)

    assert [(candidate.market_ticker, candidate.reason) for candidate in repaired] == [
        ("KXBAD", "missing_counter_evidence"),
        ("KXSAME", "no_reliable_source_path"),
    ]
    assert [
        (
            candidate.market_ticker,
            candidate.reason,
            candidate.next_state,
            candidate.terminal_reason,
        )
        for candidate in repaired_tasks
    ] == [
        ("KXTIMEOUT", "research_timeout_exhausted", "continue_researching", None),
        (
            "KXSTALESOURCE",
            "missing_resolution_source",
            "untradeable",
            "no_reliable_source_path",
        ),
    ]
    with sqlite3.connect(db_path) as conn:
        rows = {
            row[0]: row[1:]
            for row in conn.execute(
                """
                SELECT market_ticker, state, cooldown_until_ts, last_skip_reason,
                       terminal_reason
                FROM research_tasks
                ORDER BY market_ticker
                """
            )
        }
    assert rows["KXBAD"] == (
        "needs_counter_evidence",
        None,
        "missing_counter_evidence",
        None,
    )
    assert rows["KXGOOD"] == ("decision_grade_candidate", None, None, None)
    assert rows["KXSAME"] == (
        "needs_research",
        rows["KXSAME"][1],
        "no_reliable_source_path",
        None,
    )
    assert rows["KXSAME"][1] is not None
    assert rows["KXSTALEDOSSIER"] == (
        "needs_research",
        None,
        "no_reliable_source_path",
        None,
    )
    assert rows["KXNEUTRAL"] == (
        "needs_counter_evidence",
        "2026-06-29T11:00:00Z",
        "neutral_only_evidence",
        None,
    )
    assert rows["KXOFFICIALSLOW"][0] == "needs_research"
    assert rows["KXOFFICIALSLOW"][2] == "official_data_pending"
    assert rows["KXOFFICIALSLOW"][3] is None
    assert rows["KXSTALESOURCE"] == (
        "untradeable",
        None,
        "missing_resolution_source",
        "no_reliable_source_path",
    )
    assert rows["KXTIMEOUT"] == (
        "continue_researching",
        rows["KXTIMEOUT"][1],
        "research_timeout_exhausted",
        None,
    )
    assert rows["KXTIMEOUT"][1] is not None
    assert rows["KXTIMEOUTCHURN"] == (
        "continue_researching",
        None,
        "research_timeout_exhausted",
        None,
    )
    with sqlite3.connect(db_path) as conn:
        run_status = conn.execute(
            """
            SELECT verdict_status
            FROM research_runs
            WHERE market_ticker = 'KXSTALEDOSSIER'
            """
        ).fetchone()[0]
        dossier_status = conn.execute(
            """
            SELECT last_verdict_status, last_skip_reason, last_decision_grade_status
            FROM research_dossiers
            WHERE market_ticker = 'KXSTALEDOSSIER'
            """
        ).fetchone()
    assert run_status == "decision_grade_candidate"
    assert dossier_status == (
        "decision_grade_candidate",
        None,
        "decision_grade_candidate",
    )
    assert find_invalid_decision_grade_candidates(db_path) == []
    assert find_repairable_research_tasks(db_path) == []


def test_invalid_candidate_repair_preserves_backoff_for_requeued_research(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "evidence_store.db"
    _write_repair_db(db_path)

    repair_invalid_decision_grade_candidates(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = {
            row[0]: row[1:]
            for row in conn.execute(
                """
                SELECT market_ticker, state, cooldown_until_ts, backoff_seconds,
                       last_skip_reason
                FROM research_tasks
                WHERE market_ticker IN ('KXBAD', 'KXSAME', 'KXSTALEDOSSIER')
                ORDER BY market_ticker
                """
            )
        }

    assert rows["KXBAD"] == (
        "needs_counter_evidence",
        None,
        0.0,
        "missing_counter_evidence",
    )
    for ticker in ("KXSAME",):
        state, cooldown_until_ts, backoff_seconds, reason = rows[ticker]
        assert state == "needs_research"
        assert reason == "no_reliable_source_path"
        assert cooldown_until_ts is not None
        assert backoff_seconds == RESEARCH_TASK_INITIAL_BACKOFF_SECONDS
    assert rows["KXSTALEDOSSIER"] == (
        "needs_research",
        None,
        0.0,
        "no_reliable_source_path",
    )


def test_repair_timeout_rows_restore_backoff(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence_store.db"
    _write_repair_db(db_path)

    repair_research_task_blockers(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = {
            row[0]: row[1:]
            for row in conn.execute(
                """
                SELECT market_ticker, backoff_seconds, cooldown_until_ts
                FROM research_tasks
                WHERE market_ticker IN ('KXTIMEOUT', 'KXTIMEOUTCHURN')
                ORDER BY market_ticker
                """
            )
        }

    assert rows["KXTIMEOUT"][0] > 0
    assert rows["KXTIMEOUT"][1] is not None
    assert rows["KXTIMEOUTCHURN"] == (0.0, None)


def test_repair_official_pending_rows_clamps_backoff(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence_store.db"
    _write_repair_db(db_path)

    repair_research_task_blockers(db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT backoff_seconds, cooldown_until_ts
            FROM research_tasks
            WHERE market_ticker = 'KXOFFICIALSLOW'
            """
        ).fetchone()

    assert row == (21600.0, "2026-07-02T18:00:00Z")


def test_invalid_candidate_scan_ignores_superseded_historical_run(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence_store.db"
    _write_repair_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO research_runs VALUES (
                'rr-good-old-invalid', 'KXGOOD', 'decision_grade_candidate', 'yes',
                0.64, 0.74, 0.51, 0.12, '2026-06-28T10:00:00Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_run_queries VALUES (
                'rr-good-old-invalid', 0, 'old query', 'supporting', 'web'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_evidence VALUES (
                'rr-good-old-invalid-1', 'KXGOOD', 'rr-good-old-invalid',
                'web', 'source', 'https://example.com/old', 'supporting', 'yes',
                0.9, '2026-06-28T10:00:00Z', '2026-06-28T10:00:00Z', 'fp-old'
            )
            """
        )

    candidates = find_invalid_decision_grade_candidates(db_path)

    assert all(candidate.research_run_id != "rr-good-old-invalid" for candidate in candidates)


def test_invalid_candidate_apply_reports_concurrent_state_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "evidence_store.db"
    _write_repair_db(db_path)
    original_find = repair_module.find_invalid_decision_grade_candidates

    def find_then_advance(path: Path):
        candidates = original_find(path)
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                UPDATE research_tasks
                SET state = 'continue_researching',
                    last_skip_reason = 'newer_runtime_state',
                    updated_ts = '2026-06-29T10:05:00Z'
                WHERE market_ticker = 'KXBAD'
                """
            )
        return candidates

    monkeypatch.setattr(
        repair_module,
        "find_invalid_decision_grade_candidates",
        find_then_advance,
    )

    results = repair_invalid_decision_grade_candidates(db_path)

    bad = next(result for result in results if result.market_ticker == "KXBAD")
    assert bad.applied is False
    assert bad.apply_error == "stale_current_state"
    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            "SELECT state, last_skip_reason FROM research_tasks WHERE market_ticker = 'KXBAD'"
        ).fetchone()
    assert state == ("continue_researching", "newer_runtime_state")


def test_blocker_apply_reports_concurrent_newer_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "evidence_store.db"
    _write_repair_db(db_path)
    original_find = repair_module.find_repairable_research_tasks

    def find_then_advance(path: Path):
        candidates = original_find(path)
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                UPDATE research_tasks
                SET last_skip_reason = 'newer_runtime_reason',
                    same_reason_count = 1,
                    updated_ts = '2026-06-29T10:05:00Z'
                WHERE market_ticker = 'KXSTALESOURCE'
                """
            )
        return candidates

    monkeypatch.setattr(repair_module, "find_repairable_research_tasks", find_then_advance)

    results = repair_research_task_blockers(db_path)

    stale = next(result for result in results if result.market_ticker == "KXSTALESOURCE")
    assert stale.applied is False
    assert stale.apply_error == "stale_current_state"
    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            """
            SELECT state, last_skip_reason, same_reason_count
            FROM research_tasks
            WHERE market_ticker = 'KXSTALESOURCE'
            """
        ).fetchone()
    assert state == ("needs_research", "newer_runtime_reason", 1)


@pytest.mark.parametrize(
    ("branch", "task_columns", "insert_sql"),
    [
        (
            "timeout",
            "terminal_reason TEXT, last_skip_reason TEXT",
            """
            INSERT INTO research_tasks VALUES (
                'KXTIMEOUT', 'untradeable',
                'research_timeout_exhausted', 'research_timeout'
            )
            """,
        ),
        (
            "official_pending",
            "last_skip_reason TEXT, updated_ts TEXT",
            """
            INSERT INTO research_tasks VALUES (
                'KXHIGHNY-26JUL12-B95', 'needs_research',
                'neutral_only_evidence', '2026-07-11T10:00:00Z'
            )
            """,
        ),
        (
            "exhausted",
            "last_skip_reason TEXT, same_reason_count INTEGER",
            """
            INSERT INTO research_tasks VALUES (
                'KXSTALESOURCE', 'needs_research',
                'missing_resolution_source', 3
            )
            """,
        ),
    ],
)
def test_find_repairable_tasks_skips_legacy_partial_schema(
    tmp_path: Path,
    branch: str,
    task_columns: str,
    insert_sql: str,
) -> None:
    db_path = tmp_path / f"{branch}.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            f"""
            CREATE TABLE research_runs (research_run_id TEXT);
            CREATE TABLE research_evidence (evidence_id TEXT);
            CREATE TABLE research_tasks (
                market_ticker TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                {task_columns}
            );
            {insert_sql}
            """
        )

    assert find_repairable_research_tasks(db_path) == []
