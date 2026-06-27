from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.research_rollout_gate import evaluate_research_rollout
from tests._helpers import write_jsonl


NOW = datetime(2026, 5, 10, 23, 0, tzinfo=timezone.utc)


def _write_research_db(
    repo_root: Path,
    *,
    ticker: str = "KX-READY",
    run_id: str = "run-1",
    source_classes: tuple[str, str] = ("resolution_source", "corroborating_source"),
) -> None:
    db_path = repo_root / "data" / "evidence_store.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE research_dossiers (
                market_ticker TEXT PRIMARY KEY,
                last_research_run_id TEXT,
                last_researched_ts TEXT NOT NULL,
                last_verdict_status TEXT NOT NULL,
                last_skip_reason TEXT,
                last_force_side TEXT,
                last_estimated_probability REAL,
                last_confidence REAL,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE research_evidence (
                id INTEGER PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                source_class TEXT NOT NULL,
                url TEXT,
                title TEXT,
                retrieved_at TEXT,
                inserted_at TEXT NOT NULL
            );
            INSERT INTO research_dossiers VALUES (
                '%s',
                '%s',
                '2026-05-10T22:45:00+00:00',
                'trade_candidate',
                NULL,
                'yes',
                0.63,
                0.71,
                '2026-05-10T22:45:00+00:00',
                '2026-05-10T22:45:00+00:00'
            );
            INSERT INTO research_evidence (
                market_ticker,
                research_run_id,
                source_class,
                url,
                title,
                retrieved_at,
                inserted_at
            ) VALUES
            (
                '%s',
                '%s',
                '%s',
                'https://example.test/resolution',
                'Resolution',
                '2026-05-10T22:46:00+00:00',
                '2026-05-10T22:46:00+00:00'
            ),
            (
                '%s',
                '%s',
                '%s',
                'https://example.test/corroboration',
                'Corroboration',
                '2026-05-10T22:47:00+00:00',
                '2026-05-10T22:47:00+00:00'
            );
            """
            % (
                ticker,
                run_id,
                ticker,
                run_id,
                source_classes[0],
                ticker,
                run_id,
                source_classes[1],
            )
        )


def test_rollout_gate_fails_closed_when_research_mode_is_off_and_no_evidence(tmp_path):
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        trades,
        [
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": "2026-05-10T22:30:00+00:00",
                "ticker": "KX-MISS",
                "keywords": [],
                "pre_llm_would_block_and_useful": True,
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
            },
        ],
    )

    assessment = evaluate_research_rollout(tmp_path, trades, now=NOW)

    assert not assessment.ok
    assert any("REAL_WEB_RESEARCH_MODE inactive" in item for item in assessment.failures)
    assert any("no recent research_* rows" in item for item in assessment.failures)
    assert any("research dossier database missing" in item for item in assessment.failures)
    assert assessment.prewarm_backlog == ["KX-MISS"]


def test_rollout_gate_passes_only_with_active_mode_recent_research_and_expected_version(
    tmp_path,
):
    (tmp_path / ".env").write_text(
        "REAL_WEB_RESEARCH_MODE=shadow\nENABLE_RESEARCH_PREWARM_TASK=true\n",
        encoding="utf-8",
    )
    bot_log = tmp_path / "logs" / "app" / "bot.log"
    bot_log.parent.mkdir(parents=True, exist_ok=True)
    bot_log.write_text(
        "2026-05-10 22:00:00,000 UTC INFO [BOOT] version=0.99.0 pid=123\n",
        encoding="utf-8",
    )
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        trades,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "reason": "no_keywords",
                "research_attempted": True,
                "research_status": "trade_candidate",
                "research_run_id": "run-1",
            },
        ],
    )
    _write_research_db(tmp_path)

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert assessment.ok
    assert assessment.failures == []
    assert assessment.mode == "shadow"
    assert assessment.research_rows == 1
    assert assessment.fresh_evidence_rows_24h == 2


def test_rollout_gate_rejects_fresh_evidence_without_live_cache_eligible_dossier(
    tmp_path,
):
    (tmp_path / ".env").write_text(
        "REAL_WEB_RESEARCH_MODE=shadow\nENABLE_RESEARCH_PREWARM_TASK=true\n",
        encoding="utf-8",
    )
    bot_log = tmp_path / "logs" / "app" / "bot.log"
    bot_log.parent.mkdir(parents=True, exist_ok=True)
    bot_log.write_text(
        "2026-05-10 22:00:00,000 UTC INFO [BOOT] version=0.99.0 pid=123\n",
        encoding="utf-8",
    )
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        trades,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "reason": "no_keywords",
                "research_attempted": True,
                "research_status": "trade_candidate",
                "research_run_id": "run-1",
            },
        ],
    )
    _write_research_db(
        tmp_path,
        source_classes=("corroborating_source", "derived_context"),
    )

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert any("no live-cache-eligible researched dossiers" in item for item in assessment.failures)


def test_rollout_gate_rejects_operational_error_research_rows(tmp_path):
    (tmp_path / ".env").write_text(
        "REAL_WEB_RESEARCH_MODE=shadow\nENABLE_RESEARCH_PREWARM_TASK=true\n",
        encoding="utf-8",
    )
    bot_log = tmp_path / "logs" / "app" / "bot.log"
    bot_log.parent.mkdir(parents=True, exist_ok=True)
    bot_log.write_text(
        "2026-05-10 22:00:00,000 UTC INFO [BOOT] version=0.99.0 pid=123\n",
        encoding="utf-8",
    )
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        trades,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "reason": "no_keywords",
                "research_attempted": True,
                "research_status": "research_operational_error",
                "research_run_id": "run-1",
            },
        ],
    )
    _write_research_db(tmp_path)

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert any("no successful recent research rows" in item for item in assessment.failures)


def test_rollout_gate_requires_explicit_restart_version_proof(tmp_path):
    (tmp_path / ".env").write_text(
        "REAL_WEB_RESEARCH_MODE=shadow\nENABLE_RESEARCH_PREWARM_TASK=true\n",
        encoding="utf-8",
    )
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        trades,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "reason": "no_keywords",
                "research_attempted": True,
                "research_status": "trade_candidate",
                "research_run_id": "run-1",
            },
        ],
    )
    _write_research_db(tmp_path)

    assessment = evaluate_research_rollout(tmp_path, trades, now=NOW)

    assert not assessment.ok
    assert any("expected deployed version not supplied" in item for item in assessment.failures)


def test_rollout_gate_does_not_fail_prewarm_off_for_already_researched_backlog(
    tmp_path,
):
    (tmp_path / ".env").write_text("REAL_WEB_RESEARCH_MODE=shadow\n", encoding="utf-8")
    bot_log = tmp_path / "logs" / "app" / "bot.log"
    bot_log.parent.mkdir(parents=True, exist_ok=True)
    bot_log.write_text(
        "2026-05-10 22:00:00,000 UTC INFO [BOOT] version=0.99.0 pid=123\n",
        encoding="utf-8",
    )
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        trades,
        [
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": "2026-05-10T22:20:00+00:00",
                "ticker": "KX-READY",
                "keywords": [],
                "pre_llm_would_block_and_useful": True,
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "reason": "no_keywords",
                "research_attempted": True,
                "research_status": "trade_candidate",
                "research_run_id": "run-1",
            },
        ],
    )
    _write_research_db(tmp_path)

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert assessment.ok
    assert assessment.unresolved_prewarm_backlog == []


def test_rollout_gate_requires_same_ticker_live_cache_evidence_for_backlog(
    tmp_path,
):
    (tmp_path / ".env").write_text("REAL_WEB_RESEARCH_MODE=shadow\n", encoding="utf-8")
    bot_log = tmp_path / "logs" / "app" / "bot.log"
    bot_log.parent.mkdir(parents=True, exist_ok=True)
    bot_log.write_text(
        "2026-05-10 22:00:00,000 UTC INFO [BOOT] version=0.99.0 pid=123\n",
        encoding="utf-8",
    )
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        trades,
        [
            {
                "type": "SIGNAL_ANALYSIS_DETAIL",
                "ts": "2026-05-10T22:20:00+00:00",
                "ticker": "KX-A",
                "keywords": [],
                "pre_llm_would_block_and_useful": True,
                "pre_llm_gate_reason": "insufficient_semantic_overlap",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-A",
                "reason": "no_keywords",
                "research_attempted": True,
                "research_status": "trade_candidate",
                "research_run_id": "run-1",
            },
        ],
    )
    _write_research_db(tmp_path, ticker="KX-B")

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert assessment.unresolved_prewarm_backlog == ["KX-A"]


def test_rollout_gate_requires_same_research_run_for_log_and_dossier(tmp_path):
    (tmp_path / ".env").write_text(
        "REAL_WEB_RESEARCH_MODE=shadow\nENABLE_RESEARCH_PREWARM_TASK=true\n",
        encoding="utf-8",
    )
    bot_log = tmp_path / "logs" / "app" / "bot.log"
    bot_log.parent.mkdir(parents=True, exist_ok=True)
    bot_log.write_text(
        "2026-05-10 22:00:00,000 UTC INFO [BOOT] version=0.99.0 pid=123\n",
        encoding="utf-8",
    )
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        trades,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "reason": "no_keywords",
                "research_attempted": True,
                "research_status": "trade_candidate",
                "research_run_id": "run-2",
            },
        ],
    )
    _write_research_db(tmp_path, ticker="KX-READY", run_id="run-1")

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert any(
        "no successful recent research rows with matching live-cache dossier evidence"
        in item
        for item in assessment.failures
    )


def test_rollout_gate_cli_exits_nonzero_with_defensive_failures(tmp_path):
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        trades,
        [
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": "2026-05-10T22:15:00+00:00",
                "ticker": "KX-NOKEY",
                "verdict": "false_positive_neutral",
                "keyword_count": 0,
            },
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/research_rollout_gate.py",
            "--home",
            str(tmp_path),
            "--trades-log",
            str(trades),
            "--now",
            NOW.isoformat(),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 1
    assert "Research rollout gate: FAIL" in result.stdout
    assert "REAL_WEB_RESEARCH_MODE inactive" in result.stdout
    assert "prewarm_backlog: 1" in result.stdout
