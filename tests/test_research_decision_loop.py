from __future__ import annotations

import json
import sqlite3
import sys
from types import SimpleNamespace
from pathlib import Path

import scripts.research_decision_loop as decision_loop
from scripts.research_decision_loop import (
    classify_decision_loop,
    evaluate_research_decision_loop,
    main,
    _blocker_reasons,
    _shadow_prewarm_command,
    _summarize_shadow_prewarm_result,
)
from scripts.research_decision_grade_repair import RepairableResearchTask
from scripts.research_decision_grade_repair import find_repairable_research_tasks
from tests.test_research_profit_validation_loop import (
    NOW,
    _base_paths,
    _write_evidence_store,
    _write_paper_db,
    _write_trade_log,
)


def test_decision_loop_describes_stale_researchable_backlog_without_blocked_label():
    reasons = _blocker_reasons(
        {
            "blocked_by_no_reliable_source_path": 2,
            "stale_but_researchable": 3,
        }
    )

    assert "2 dossier(s) blocked by no_reliable_source_path" in reasons
    assert "3 dossier(s) stale but researchable; keep queued for refresh" in reasons
    assert "3 dossier(s) blocked by stale_but_researchable" not in reasons


def test_decision_loop_describes_ambiguous_direction_as_side_probability_gap():
    reasons = _blocker_reasons({"blocked_by_ambiguous_direction": 2})

    assert (
        "2 dossier(s) have source coverage but no side/probability decision"
        in reasons
    )
    assert "2 dossier(s) blocked by ambiguous_direction" not in reasons


def test_market_ineligible_blocker_keeps_decision_loop_in_research() -> None:
    decision_grade = SimpleNamespace(
        decision_grade_candidates=0,
        terminal_untradeable=0,
        blocked_by_market_ineligible=1,
    )
    validation = SimpleNamespace(
        decision_grade=decision_grade,
        funnel=SimpleNamespace(live_cache_eligible=0),
        risk=SimpleNamespace(unauthorized_live_orders=0),
        runtime=SimpleNamespace(error_critical_count=0, within_cooldown_repeats=0),
        workflow=SimpleNamespace(
            normal_gate_ok=True,
            strict_live_cache_gate_ok=True,
        ),
        verdict="NOT_SUCCESSFUL",
        action="ROLL_BACK_OR_PATCH",
        research_operating_cleanly=True,
        research_supports_trades=False,
        reasons=["market ineligible"],
    )

    report = classify_decision_loop(validation, [], [])

    assert report.status == "CONTINUE_RESEARCH"
    assert report.ok is False
    assert report.research_blockers == {"blocked_by_market_ineligible": 1}
    assert report.to_dict()["research_blockers"] == {
        "blocked_by_market_ineligible": 1
    }


def test_decision_loop_describes_mixed_repairable_task_reasons():
    decision_grade = SimpleNamespace(
        decision_grade_candidates=0,
        terminal_untradeable=0,
        blocked_by_missing_price=0,
        blocked_by_no_reliable_source_path=0,
        blocked_by_official_data_pending=0,
        blocked_by_provider_error=0,
        blocked_by_neutral_evidence=0,
        blocked_by_no_counter_evidence=0,
        blocked_by_generic_summary=0,
        blocked_by_unresolved_contradiction=0,
        stale_but_researchable=0,
        terminal_timeout_exhausted=0,
    )
    validation = SimpleNamespace(
        decision_grade=decision_grade,
        funnel=SimpleNamespace(live_cache_eligible=0),
        risk=SimpleNamespace(unauthorized_live_orders=0),
        runtime=SimpleNamespace(error_critical_count=0, within_cooldown_repeats=0),
        workflow=SimpleNamespace(
            normal_gate_ok=True,
            strict_live_cache_gate_ok=False,
        ),
        verdict="PROVISIONALLY_SUCCESSFUL",
        action="CONTINUE_SHADOW",
        research_operating_cleanly=True,
        research_supports_trades=False,
        reasons=[],
    )

    report = classify_decision_loop(
        validation,
        [],
        [
            RepairableResearchTask(
                market_ticker="KXTIMEOUT",
                state="continue_researching",
                reason="research_timeout_exhausted",
                next_state="continue_researching",
            ),
            RepairableResearchTask(
                market_ticker="KXOFFICIAL",
                state="needs_research",
                reason="official_data_pending",
                next_state="needs_research",
            ),
        ],
    )

    assert "2 repairable research task(s) need queue-state cleanup" in report.reasons
    assert all("timeout task" not in reason for reason in report.reasons)


def test_future_weather_contradictory_terminal_requeues_for_official_data(
    tmp_path: Path,
) -> None:
    _trades_log, evidence_db, _paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
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
                updated_ts TEXT NOT NULL DEFAULT '2026-07-03T15:40:19Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, terminal_reason, last_skip_reason,
                same_reason_count
            ) VALUES (
                'KXHIGHNY-26JUL04-B104.5',
                'untradeable',
                'contradictory_evidence_unresolved',
                'ambiguous_direction',
                3
            )
            """
        )

    candidates = find_repairable_research_tasks(evidence_db)

    assert [
        (
            candidate.market_ticker,
            candidate.reason,
            candidate.next_state,
            candidate.terminal_reason,
        )
        for candidate in candidates
    ] == [
        (
            "KXHIGHNY-26JUL04-B104.5",
            "official_data_pending",
            "needs_research",
            None,
        )
    ]


def test_south_africa_trade_balance_ambiguous_task_requeues_for_official_data(
    tmp_path: Path,
) -> None:
    _trades_log, evidence_db, _paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
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
                updated_ts TEXT NOT NULL DEFAULT '2026-07-02T16:07:45Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, terminal_reason, last_skip_reason,
                same_reason_count
            ) VALUES (
                'KXSATRADEBAL-26JUL31-T8.00',
                'needs_counter_evidence',
                NULL,
                'ambiguous_direction',
                1
            )
            """
        )

    candidates = find_repairable_research_tasks(evidence_db)

    assert [
        (
            candidate.market_ticker,
            candidate.reason,
            candidate.next_state,
            candidate.terminal_reason,
        )
        for candidate in candidates
    ] == [
        (
            "KXSATRADEBAL-26JUL31-T8.00",
            "official_data_pending",
            "needs_research",
            None,
        )
    ]


def test_future_confirmation_neutral_terminal_requeues_for_official_data(
    tmp_path: Path,
) -> None:
    _trades_log, evidence_db, _paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
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
                updated_ts TEXT NOT NULL DEFAULT '2026-07-02T15:19:42Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, terminal_reason, last_skip_reason,
                same_reason_count
            ) VALUES (
                'KXICECONF-26JUN29-AUG01',
                'untradeable',
                'contradictory_evidence_unresolved',
                'neutral_only_evidence',
                3
            )
            """
        )

    candidates = find_repairable_research_tasks(evidence_db)

    assert [
        (
            candidate.market_ticker,
            candidate.reason,
            candidate.next_state,
            candidate.terminal_reason,
        )
        for candidate in candidates
    ] == [
        (
            "KXICECONF-26JUN29-AUG01",
            "official_data_pending",
            "needs_research",
            None,
        )
    ]


def test_decision_loop_marks_valid_dossier_ready_for_trade_review(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    _write_trade_log(
        trades_log,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-29T10:01:00Z",
                "ticker": "KXPROFIT-26JUL01",
                "research_status": "decision_grade_candidate",
                "research_run_id": "rr-profitable",
            },
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-29T10:05:00Z",
                "ticker": "KXPROFIT-26JUL01",
                "edge": 0.09,
            },
        ],
    )
    _write_paper_db(paper_db, [])

    report = evaluate_research_decision_loop(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
        run_workflow_gates=False,
        run_bothealth=False,
        run_botcheck=False,
    )

    assert report.ok is True
    assert report.status == "READY_FOR_TRADE_REVIEW"
    assert report.action == "HOLD_SHADOW_AND_REVIEW_TRADE_DECISION"
    assert report.decision_grade_candidates == 1
    assert report.live_cache_eligible == 1


def test_shadow_prewarm_result_summary_keeps_progress_counts() -> None:
    summary = _summarize_shadow_prewarm_result(
        {
            "command": "python scripts/research_prewarm.py --json",
            "returncode": 0,
            "stdout": (
                '{"attempted": 1, "evidence": 3, "markets": 1, '
                '"queries": 7, "statuses": {"continue_researching": 1}}'
            ),
            "stderr": "",
        }
    )

    assert summary == {
        "command": "python scripts/research_prewarm.py --json",
        "returncode": 0,
        "markets": 1,
        "attempted": 1,
        "queries": 7,
        "evidence": 3,
        "statuses": {"continue_researching": 1},
    }


def test_shadow_prewarm_result_summary_preserves_per_market_results() -> None:
    summary = _summarize_shadow_prewarm_result(
        {
            "command": "python scripts/research_prewarm.py --json --include-results",
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "attempted": 2,
                    "evidence": 5,
                    "markets": 2,
                    "queries": 14,
                    "statuses": {"untradeable": 1, "needs_research": 1},
                    "results": [
                        {
                            "market_ticker": "KX-A",
                            "status": "untradeable",
                            "skip_reason": "no_edge",
                        },
                        {
                            "market_ticker": "KX-B",
                            "status": "needs_research",
                            "skip_reason": "official_data_pending",
                        },
                    ],
                }
            ),
            "stderr": "",
        }
    )

    assert summary["results"] == [
        {
            "market_ticker": "KX-A",
            "status": "untradeable",
            "skip_reason": "no_edge",
        },
        {
            "market_ticker": "KX-B",
            "status": "needs_research",
            "skip_reason": "official_data_pending",
        },
    ]


def test_shadow_prewarm_result_summary_reports_unparseable_stdout() -> None:
    summary = _summarize_shadow_prewarm_result(
        {
            "command": "python scripts/research_prewarm.py --json",
            "returncode": 0,
            "stdout": "not json",
            "stderr": "",
        }
    )

    assert summary["returncode"] == 0
    assert summary["stdout_preview"] == "not json"
    assert "stdout_parse_error" in summary


def test_decision_loop_treats_explicit_untradeable_as_no_trade_decision(
    tmp_path: Path,
) -> None:
    trades_log = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    evidence_db = tmp_path / "data" / "evidence_store.db"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_evidence_store(
        evidence_db,
        verdict_status="untradeable",
        skip_reason="no_edge",
    )
    _write_trade_log(
        trades_log,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-29T10:01:00Z",
                "ticker": "KXPROFIT-26JUL01",
                "research_status": "untradeable",
                "research_skip_reason": "no_edge",
                "research_run_id": "rr-profitable",
            },
        ],
    )
    _write_paper_db(paper_db, [])

    report = evaluate_research_decision_loop(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
        run_workflow_gates=False,
        run_bothealth=False,
        run_botcheck=False,
    )

    assert report.ok is True
    assert report.status == "TERMINAL_NO_TRADE"
    assert report.action == "NO_TRADE"
    assert report.terminal_untradeable == 1


def test_decision_loop_keeps_researching_when_terminal_no_trade_has_backlog_blockers(
    tmp_path: Path,
) -> None:
    trades_log = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    evidence_db = tmp_path / "data" / "evidence_store.db"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_evidence_store(
        evidence_db,
        verdict_status="untradeable",
        skip_reason="no_edge",
    )
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
            INSERT INTO research_runs (
                research_run_id, market_ticker, trigger_headline,
                trigger_source, attempted, summary, verdict_status,
                skip_reason, force_side, estimated_probability, confidence,
                market_price, estimated_edge, decision_grade_status, created_ts
            ) VALUES (
                'rr-backlog',
                'KXBACKLOG-26JUL01',
                'headline',
                'research_prewarm',
                1,
                'missing source path',
                'needs_research',
                'missing_resolution_source',
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                'needs_research',
                '2026-06-29T10:05:00Z'
            )
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_decision_loop(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
        run_workflow_gates=False,
        run_bothealth=False,
        run_botcheck=False,
    )

    assert report.ok is False
    assert report.status == "CONTINUE_RESEARCH"
    assert report.action == "CONTINUE_SHADOW_RESEARCH"
    assert report.terminal_untradeable == 1
    assert report.research_blockers["blocked_by_no_reliable_source_path"] == 1
    assert "refresh official/rules/resolution plus reputable secondary source paths" in (
        report.next_commands
    )


def test_decision_loop_fails_closed_when_research_has_no_decision(
    tmp_path: Path,
) -> None:
    trades_log = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    paper_db = tmp_path / "data" / "paper_trades.db"
    evidence_db = tmp_path / "data" / "missing_evidence_store.db"
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_decision_loop(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
        run_workflow_gates=False,
        run_bothealth=False,
        run_botcheck=False,
    )

    assert report.ok is False
    assert report.status == "FAILED_NO_DECISION"
    assert report.action == "FAIL_CLOSED_NO_DECISION"


def test_decision_loop_routes_invalid_decision_grade_back_to_research(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
            CREATE TABLE research_tasks (
                market_ticker TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                updated_ts TEXT,
                terminal_reason TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_tasks VALUES (
                'KXPROFIT-26JUL01',
                'decision_grade_candidate',
                '2026-06-29T10:00:00Z',
                NULL
            )
            """
        )
        conn.execute(
            """
            DELETE FROM research_run_queries
            WHERE query_intent = 'disconfirming'
            """
        )
    _write_trade_log(
        trades_log,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-29T10:01:00Z",
                "ticker": "KXPROFIT-26JUL01",
                "research_status": "decision_grade_candidate",
                "research_run_id": "rr-profitable",
            }
        ],
    )
    _write_paper_db(paper_db, [])

    report = evaluate_research_decision_loop(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
        run_workflow_gates=False,
        run_bothealth=False,
        run_botcheck=False,
    )

    assert report.ok is False
    assert report.status == "CONTINUE_RESEARCH"
    assert report.invalid_decision_grade_candidates == 1
    assert report.repair_candidates[0]["reason"] == "missing_counter_evidence"
    assert any(
        "research_decision_loop.py --simulate-repair-prewarm" in cmd
        for cmd in report.next_commands
    )
    assert any("research_decision_grade_repair.py" in cmd for cmd in report.next_commands)
    assert any(
        "research_decision_loop.py --run-prewarm" in cmd
        for cmd in report.next_commands
    )


def test_decision_loop_surfaces_official_data_pending_blocker(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
            INSERT INTO research_runs (
                research_run_id, market_ticker, trigger_headline,
                trigger_source, attempted, summary, verdict_status,
                skip_reason, force_side, estimated_probability, confidence,
                market_price, estimated_edge, decision_grade_status, created_ts
            ) VALUES (
                'rr-official-pending',
                'KXOFFICIALPENDING-26JUL01',
                'headline',
                'source',
                1,
                'Official source has not published yet.',
                'needs_research',
                'official_data_pending',
                NULL,
                NULL,
                NULL,
                0.51,
                NULL,
                NULL,
                '2026-06-29T10:00:00Z'
            )
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_decision_loop(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
        run_workflow_gates=False,
        run_bothealth=False,
        run_botcheck=False,
    )

    assert report.status == "CONTINUE_RESEARCH"
    assert report.research_blockers["blocked_by_official_data_pending"] == 1
    assert any("official_data_pending" in reason for reason in report.reasons)
    assert "refresh official settlement source after publication delay" in report.next_commands


def test_decision_loop_routes_timeout_terminal_tasks_to_repair(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, terminal_reason, last_skip_reason
            ) VALUES (
                'KXTIMEOUT-26JUL01',
                'untradeable',
                'research_timeout_exhausted',
                'research_timeout'
            )
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_decision_loop(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
        run_workflow_gates=False,
        run_bothealth=False,
        run_botcheck=False,
    )

    assert report.ok is False
    assert report.status == "CONTINUE_RESEARCH"
    assert report.repairable_research_tasks == 1
    assert report.task_repair_candidates[0]["market_ticker"] == "KXTIMEOUT-26JUL01"
    assert any("research_decision_grade_repair.py --apply" in cmd for cmd in report.next_commands)


def test_decision_loop_apply_repair_requeues_repairable_blockers(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, terminal_reason, last_skip_reason
            ) VALUES (
                'KXPROFIT-26JUL01',
                'decision_grade_candidate',
                NULL,
                NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, terminal_reason, last_skip_reason
            ) VALUES (
                'KXTIMEOUT-26JUL01',
                'untradeable',
                'research_timeout_exhausted',
                'research_timeout'
            )
            """
        )
        conn.execute(
            """
            DELETE FROM research_run_queries
            WHERE query_intent = 'disconfirming'
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_decision_loop(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
        run_workflow_gates=False,
        run_bothealth=False,
        run_botcheck=False,
        apply_repair=True,
    )

    assert report.repair_applied is True
    assert report.repaired_decision_grade_candidates == 1
    assert report.repaired_research_tasks == 1
    assert report.invalid_decision_grade_candidates == 0
    assert report.repairable_research_tasks == 0
    with sqlite3.connect(evidence_db) as conn:
        rows = {
            row[0]: row[1:]
            for row in conn.execute(
                """
                SELECT market_ticker, state, terminal_reason, last_skip_reason
                FROM research_tasks
                ORDER BY market_ticker
                """
            )
        }
    assert rows["KXPROFIT-26JUL01"] == (
        "needs_counter_evidence",
        None,
        "missing_counter_evidence",
    )
    assert rows["KXTIMEOUT-26JUL01"] == (
        "continue_researching",
        None,
        "research_timeout_exhausted",
    )


def test_main_accumulates_repair_counts_across_cycles(
    tmp_path: Path,
    capsys,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, terminal_reason, last_skip_reason
            ) VALUES (
                'KXPROFIT-26JUL01',
                'decision_grade_candidate',
                NULL,
                NULL
            )
            """
        )
        conn.execute(
            """
            DELETE FROM research_run_queries
            WHERE query_intent = 'disconfirming'
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    exit_code = main(
        [
            "--repo-root",
            str(tmp_path),
            "--trades-log",
            str(trades_log),
            "--paper-db",
            str(paper_db),
            "--evidence-db",
            str(evidence_db),
            "--skip-workflow-gates",
            "--skip-bothealth",
            "--skip-botcheck",
            "--apply-repair",
            "--max-cycles",
            "2",
            "--sleep-seconds",
            "0",
            "--format",
            "json",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["repair_applied"] is True
    assert report["repaired_decision_grade_candidates"] == 1
    assert report["invalid_decision_grade_candidates"] == 0
    assert any(
        "requeued 1 invalid decision-grade candidate(s) and cleaned 0 research task(s)"
        in reason
        for reason in report["reasons"]
    )
    assert all("requeued 0 invalid" not in reason for reason in report["reasons"])


def test_simulate_repair_prewarm_uses_temp_db_without_mutating_source(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, terminal_reason, last_skip_reason
            ) VALUES (
                'KXPROFIT-26JUL01',
                'decision_grade_candidate',
                NULL,
                NULL
            )
            """
        )
        conn.execute(
            """
            DELETE FROM research_run_queries
            WHERE query_intent = 'disconfirming'
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    observed_db_paths: list[Path] = []

    def fake_shadow_prewarm(repo_root: Path, args: SimpleNamespace) -> dict:
        copied_db = decision_loop._resolve(repo_root, args.evidence_db)
        observed_db_paths.append(copied_db)
        assert copied_db != evidence_db
        with sqlite3.connect(copied_db) as conn:
            conn.execute("CREATE TABLE simulation_marker (value TEXT)")
            conn.execute("INSERT INTO simulation_marker VALUES ('touched')")
        return {
            "command": f"fake-prewarm --db-path {copied_db}",
            "returncode": 0,
            "stdout": (
                '{"markets": 1, "attempted": 1, "queries": 1, '
                '"evidence": 1, "statuses": {"needs_counter_evidence": 1}}'
            ),
            "stderr": "",
        }

    monkeypatch.setattr(
        decision_loop,
        "_run_shadow_prewarm",
        fake_shadow_prewarm,
    )

    exit_code = main(
        [
            "--repo-root",
            str(tmp_path),
            "--trades-log",
            str(trades_log),
            "--paper-db",
            str(paper_db),
            "--evidence-db",
            str(evidence_db),
            "--skip-workflow-gates",
            "--skip-bothealth",
            "--skip-botcheck",
            "--simulate-repair-prewarm",
            "--max-cycles",
            "2",
            "--format",
            "json",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["simulation_mode"] is True
    assert report["research_operating_cleanly"] is False
    assert report["research_supports_trades"] is False
    assert report["simulation_source_evidence_db"] == str(evidence_db)
    assert report["simulation_evidence_db"] != str(evidence_db)
    assert report["repair_applied"] is True
    assert report["repaired_decision_grade_candidates"] == 1
    assert report["action_results"][0]["statuses"] == {"needs_counter_evidence": 1}
    assert observed_db_paths == [Path(report["simulation_evidence_db"])]

    with sqlite3.connect(evidence_db) as conn:
        original_state = conn.execute(
            """
            SELECT state, last_skip_reason
            FROM research_tasks
            WHERE market_ticker = 'KXPROFIT-26JUL01'
            """
        ).fetchone()
    assert original_state == ("decision_grade_candidate", None)

    with sqlite3.connect(observed_db_paths[0]) as conn:
        copied_state = conn.execute(
            """
            SELECT state, last_skip_reason
            FROM research_tasks
            WHERE market_ticker = 'KXPROFIT-26JUL01'
            """
        ).fetchone()
        marker = conn.execute("SELECT value FROM simulation_marker").fetchone()
    assert copied_state == ("needs_counter_evidence", "missing_counter_evidence")
    assert marker == ("touched",)


def test_decision_loop_cli_exits_nonzero_for_no_decision(tmp_path: Path) -> None:
    trades_log = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    paper_db = tmp_path / "data" / "paper_trades.db"
    evidence_db = tmp_path / "data" / "missing_evidence_store.db"
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    exit_code = main(
        [
            "--repo-root",
            str(tmp_path),
            "--trades-log",
            str(trades_log),
            "--paper-db",
            str(paper_db),
            "--evidence-db",
            str(evidence_db),
            "--now",
            "2026-06-29T12:00:00Z",
            "--skip-workflow-gates",
            "--skip-bothealth",
            "--skip-botcheck",
            "--format",
            "json",
        ]
    )

    assert exit_code == 1


def test_shadow_prewarm_command_is_shadow_only(tmp_path: Path) -> None:
    command = _shadow_prewarm_command(
        tmp_path,
        SimpleNamespace(
            evidence_db=Path("data/evidence_store.db"),
            prewarm_max_markets=3,
            prewarm_max_pages=2,
            prewarm_max_queries=7,
            prewarm_timeout_seconds=20.0,
        ),
    )

    assert command[:2] == [
        sys.executable,
        str(tmp_path / "scripts" / "research_prewarm.py"),
    ]
    assert "--db-path" in command
    assert str(tmp_path / "data" / "evidence_store.db") in command
    assert "--json" in command
    assert "--include-results" in command
    assert "--no-trade-log" in command
