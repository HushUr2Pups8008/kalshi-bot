from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.research_rollout_gate import evaluate_research_rollout
from tests._helpers import write_jsonl


NOW = datetime(2026, 5, 10, 23, 0, tzinfo=timezone.utc)
RESEARCH_ENV_KEYS = (
    "REAL_WEB_RESEARCH_MODE",
    "ENABLE_RESEARCH_PREWARM_TASK",
    "REAL_WEB_RESEARCH_MAX_QUERIES",
    "REAL_WEB_RESEARCH_TIMEOUT_SECONDS",
    "RESEARCH_PREWARM_INTERVAL_SECONDS",
    "RESEARCH_PREWARM_MAX_MARKETS",
    "RESEARCH_PREWARM_MAX_PAGES",
    "RESEARCH_PREWARM_CONCURRENCY",
    "RESEARCH_PREWARM_TARGET_COOLDOWN_SECONDS",
)


@pytest.fixture(autouse=True)
def _clear_research_env(monkeypatch):
    for key in RESEARCH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _write_research_db(
    repo_root: Path,
    *,
    ticker: str = "KX-READY",
    run_id: str = "run-1",
    contract_fingerprint: str = "contract-v1",
    source_classes: tuple[str, str] = ("resolution_source", "reputable_secondary"),
    verdict_status: str = "decision_grade_candidate",
    skip_reason: str | None = None,
    market_status: str | None = "active",
    market_close_time: str | None = "2026-05-11T23:00:00Z",
) -> None:
    db_path = repo_root / "data" / "evidence_store.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE research_dossiers (
                market_ticker TEXT PRIMARY KEY,
                last_research_run_id TEXT,
                last_contract_fingerprint TEXT,
                market_status TEXT,
                market_close_time TEXT,
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
            CREATE TABLE research_runs (
                research_run_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                verdict_status TEXT NOT NULL,
                force_side TEXT,
                estimated_probability REAL,
                confidence REAL,
                market_price REAL,
                estimated_edge REAL,
                created_ts TEXT NOT NULL
            );
            CREATE TABLE research_evidence (
                id INTEGER PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                contract_fingerprint TEXT NOT NULL,
                source_class TEXT NOT NULL,
                source_name TEXT,
                source_url TEXT,
                title TEXT,
                claim_type TEXT,
                supports_direction TEXT,
                supports_confidence REAL,
                retrieved_at TEXT,
                inserted_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO research_dossiers VALUES (
                ?, ?, ?, ?, ?, '2026-05-10T22:45:00+00:00', ?, ?,
                'yes', 0.63, 0.71, 0.51, 0.11, ?,
                '2026-05-10T22:45:00+00:00',
                '2026-05-10T22:45:00+00:00'
            )
            """,
            (
                ticker,
                run_id,
                contract_fingerprint,
                market_status,
                market_close_time,
                verdict_status,
                skip_reason or "",
                (
                    verdict_status
                    if verdict_status == "decision_grade_candidate"
                    else ""
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO research_runs VALUES (
                ?, ?, ?, 'yes', 0.63, 0.71, 0.51, 0.11,
                '2026-05-10T22:45:00+00:00'
            )
            """,
            (run_id, ticker, verdict_status),
        )
        conn.executemany(
            """
            INSERT INTO research_evidence (
                market_ticker,
                research_run_id,
                contract_fingerprint,
                source_class,
                source_name,
                source_url,
                title,
                claim_type,
                supports_direction,
                supports_confidence,
                retrieved_at,
                inserted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    ticker,
                    run_id,
                    contract_fingerprint,
                    source_classes[0],
                    "Official",
                    "https://official.test/resolution",
                    "Resolution",
                    "settlement",
                    "yes",
                    0.8,
                    "2026-05-10T22:46:00+00:00",
                    "2026-05-10T22:46:00+00:00",
                ),
                (
                    ticker,
                    run_id,
                    contract_fingerprint,
                    source_classes[1],
                    "Wire",
                    "https://wire.test/corroboration",
                    "Counter",
                    "disconfirming",
                    "no",
                    0.8,
                    "2026-05-10T22:47:00+00:00",
                    "2026-05-10T22:47:00+00:00",
                ),
            ),
        )


def _write_candidate_runtime(repo_root: Path) -> tuple[Path, Path]:
    (repo_root / ".env").write_text(
        "REAL_WEB_RESEARCH_MODE=shadow\nENABLE_RESEARCH_PREWARM_TASK=true\n",
        encoding="utf-8",
    )
    bot_log = repo_root / "logs" / "app" / "bot.log"
    bot_log.parent.mkdir(parents=True, exist_ok=True)
    bot_log.write_text(
        "2026-05-10 22:00:00,000 UTC INFO [BOOT] version=0.99.0 pid=123\n",
        encoding="utf-8",
    )
    trades = repo_root / "logs" / "trades" / "live" / "trades.jsonl"
    write_jsonl(
        trades,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:55:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
            }
        ],
    )
    return trades, bot_log


@pytest.mark.parametrize(
    ("market_status", "market_close_time"),
    [
        ("active", "2026-05-10T23:00:00Z"),
        ("closed", "2026-05-11T23:00:00Z"),
        (None, "2026-05-11T23:00:00Z"),
        ("active", None),
    ],
)
def test_rollout_gate_rejects_market_ineligible_candidate(
    tmp_path: Path,
    market_status: str | None,
    market_close_time: str | None,
) -> None:
    trades, bot_log = _write_candidate_runtime(tmp_path)
    _write_research_db(
        tmp_path,
        market_status=market_status,
        market_close_time=market_close_time,
    )

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert assessment.matched_research_proofs == 0
    assert assessment.live_cache_eligible_dossiers == 0


def test_rollout_gate_rejects_missing_market_eligibility_schema(tmp_path: Path) -> None:
    trades, bot_log = _write_candidate_runtime(tmp_path)
    _write_research_db(tmp_path)
    with sqlite3.connect(tmp_path / "data" / "evidence_store.db") as conn:
        conn.execute("ALTER TABLE research_dossiers DROP COLUMN market_status")
        conn.execute("ALTER TABLE research_dossiers DROP COLUMN market_close_time")

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert assessment.matched_research_proofs == 0
    assert assessment.live_cache_eligible_dossiers == 0


def test_rollout_gate_requires_source_class_diversity(tmp_path: Path) -> None:
    trades, bot_log = _write_candidate_runtime(tmp_path)
    _write_research_db(
        tmp_path,
        source_classes=("official_primary", "official_primary"),
    )

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert assessment.matched_research_proofs == 0
    assert assessment.live_cache_eligible_dossiers == 0


def test_unrelated_terminal_proof_does_not_excuse_ineligible_candidate(
    tmp_path: Path,
) -> None:
    trades, bot_log = _write_candidate_runtime(tmp_path)
    write_jsonl(
        trades,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:54:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
            },
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:55:00+00:00",
                "ticker": "KX-OTHER",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "untradeable",
                "research_skip_reason": "no_edge",
                "research_run_id": "run-other-terminal",
                "research_contract_fingerprint": "contract-other",
            },
        ],
    )
    _write_research_db(
        tmp_path,
        market_status="active",
        market_close_time="2026-05-10T23:00:00Z",
    )

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert any(
        "no active market-eligible decision-grade proof" in item
        for item in assessment.failures
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
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
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


def test_rollout_gate_accepts_prewarm_result_research_proof(tmp_path):
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
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
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
    assert assessment.research_rows == 1
    assert assessment.matched_research_proofs == 1


def test_rollout_gate_rejects_legacy_trade_candidate_without_decision_grade(tmp_path):
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
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "trade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
            },
        ],
    )
    _write_research_db(tmp_path, verdict_status="trade_candidate")

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert assessment.successful_research_rows == 0
    assert assessment.matched_research_proofs == 0
    assert any("no successful recent research rows" in item for item in assessment.failures)


def test_rollout_gate_accepts_terminal_untradeable_research_without_live_cache(tmp_path):
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
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "untradeable",
                "research_skip_reason": "no_edge",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
            },
        ],
    )
    _write_research_db(tmp_path, verdict_status="untradeable", skip_reason="no_edge")

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert assessment.ok
    assert assessment.failures == []
    assert assessment.successful_research_rows == 1
    assert assessment.matched_research_proofs == 0
    assert assessment.live_cache_eligible_dossiers == 0


def test_rollout_gate_accepts_terminal_source_path_no_trade_without_live_cache(
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
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "untradeable",
                "research_skip_reason": "no_reliable_source_path",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
            },
        ],
    )
    _write_research_db(
        tmp_path,
        verdict_status="untradeable",
        skip_reason="no_reliable_source_path",
    )

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert assessment.ok
    assert assessment.failures == []
    assert assessment.successful_research_rows == 1
    assert assessment.matched_research_proofs == 0
    assert assessment.live_cache_eligible_dossiers == 0


def test_rollout_gate_rejects_unknown_untradeable_as_success(tmp_path):
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
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "untradeable",
                "research_skip_reason": "insufficient_evidence",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
            },
        ],
    )
    _write_research_db(
        tmp_path,
        verdict_status="untradeable",
        skip_reason="insufficient_evidence",
    )

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert assessment.successful_research_rows == 0
    assert any("no successful recent research rows" in item for item in assessment.failures)


def test_rollout_gate_ignores_decision_grade_candidate_superseded_by_terminal_run(
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
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:10:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-old-candidate",
                "research_contract_fingerprint": "contract-v1",
            },
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "untradeable",
                "research_skip_reason": "no_edge",
                "research_run_id": "run-terminal",
                "research_contract_fingerprint": "contract-v1",
            },
        ],
    )
    db_path = tmp_path / "data" / "evidence_store.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
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
            CREATE TABLE research_runs (
                research_run_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                verdict_status TEXT NOT NULL,
                force_side TEXT,
                estimated_probability REAL,
                confidence REAL,
                market_price REAL,
                estimated_edge REAL,
                created_ts TEXT NOT NULL
            );
            CREATE TABLE research_evidence (
                id INTEGER PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                contract_fingerprint TEXT NOT NULL,
                source_class TEXT NOT NULL,
                url TEXT,
                title TEXT,
                retrieved_at TEXT,
                inserted_at TEXT NOT NULL
            );
            INSERT INTO research_dossiers VALUES (
                'KX-READY',
                'run-terminal',
                'contract-v1',
                '2026-05-10T22:50:00+00:00',
                'untradeable',
                'no_edge',
                'yes',
                0.63,
                0.71,
                0.51,
                0.11,
                'untradeable',
                '2026-05-10T22:50:00+00:00',
                '2026-05-10T22:00:00+00:00'
            );
            INSERT INTO research_runs VALUES (
                'run-old-candidate',
                'KX-READY',
                'decision_grade_candidate',
                'yes',
                0.63,
                0.71,
                0.51,
                0.11,
                '2026-05-10T22:10:00+00:00'
            );
            INSERT INTO research_runs VALUES (
                'run-terminal',
                'KX-READY',
                'untradeable',
                'yes',
                0.63,
                0.71,
                0.51,
                0.11,
                '2026-05-10T22:50:00+00:00'
            );
            INSERT INTO research_evidence (
                market_ticker,
                research_run_id,
                contract_fingerprint,
                source_class,
                url,
                title,
                retrieved_at,
                inserted_at
            ) VALUES
            (
                'KX-READY',
                'run-old-candidate',
                'contract-v1',
                'resolution_source',
                'https://example.test/old-resolution',
                'Old resolution',
                '2026-05-10T16:00:00+00:00',
                '2026-05-10T22:10:00+00:00'
            ),
            (
                'KX-READY',
                'run-terminal',
                'contract-v1',
                'resolution_source',
                'https://example.test/current-resolution',
                'Current resolution',
                '2026-05-10T22:50:00+00:00',
                '2026-05-10T22:50:00+00:00'
            );
            """
        )

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert assessment.ok
    assert assessment.failures == []
    assert assessment.successful_research_rows == 2
    assert assessment.matched_research_proofs == 0
    assert assessment.live_cache_eligible_dossiers == 0


def test_rollout_gate_accepts_decision_grade_candidate_research_proof(tmp_path):
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
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
            },
        ],
    )
    _write_research_db(tmp_path, verdict_status="decision_grade_candidate")

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert assessment.ok
    assert assessment.failures == []
    assert assessment.successful_research_rows == 1
    assert assessment.matched_research_proofs == 1
    assert assessment.live_cache_eligible_dossiers == 1


def test_rollout_gate_accepts_persisted_live_cache_candidate_after_restart(tmp_path):
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
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:55:00+00:00",
                "ticker": "KX-OTHER",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "needs_research",
                "research_skip_reason": "official_data_pending",
            },
        ],
    )
    _write_research_db(tmp_path, verdict_status="decision_grade_candidate")

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert assessment.ok
    assert assessment.successful_research_rows == 0
    assert assessment.matched_research_proofs == 0
    assert assessment.live_cache_eligible_dossiers == 1
    assert assessment.failures == []


def test_rollout_gate_rejects_persisted_candidate_with_same_side_counter(
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
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:55:00+00:00",
                "ticker": "KX-OTHER",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "needs_research",
                "research_skip_reason": "official_data_pending",
            },
        ],
    )
    _write_research_db(tmp_path, verdict_status="decision_grade_candidate")
    with sqlite3.connect(tmp_path / "data" / "evidence_store.db") as conn:
        conn.execute(
            """
            UPDATE research_evidence
            SET claim_type = CASE
                    WHEN source_class = 'resolution_source' THEN 'settlement'
                    ELSE 'disconfirming'
                END,
                supports_direction = 'yes'
            """
        )

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert assessment.matched_research_proofs == 0
    assert assessment.live_cache_eligible_dossiers == 0
    assert any("no successful recent research rows" in item for item in assessment.failures)


def test_rollout_gate_rejects_candidate_run_after_latest_snapshot_downgrade(tmp_path):
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
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-05-10T22:50:00+00:00",
                "ticker": "KX-READY",
                "research_prewarm": True,
                "research_attempted": True,
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-candidate",
                "research_contract_fingerprint": "contract-v1",
            },
        ],
    )
    db_path = tmp_path / "data" / "evidence_store.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
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
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE research_runs (
                research_run_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                trigger_headline TEXT NOT NULL,
                trigger_source TEXT NOT NULL,
                attempted INTEGER NOT NULL,
                summary TEXT NOT NULL,
                verdict_status TEXT NOT NULL,
                skip_reason TEXT,
                force_side TEXT,
                estimated_probability REAL,
                confidence REAL,
                market_price REAL,
                estimated_edge REAL,
                created_ts TEXT
            );
            CREATE TABLE research_evidence (
                id INTEGER PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                contract_fingerprint TEXT NOT NULL,
                source_class TEXT NOT NULL,
                url TEXT,
                title TEXT,
                retrieved_at TEXT,
                inserted_at TEXT NOT NULL
            );
            INSERT INTO research_dossiers VALUES (
                'KX-READY',
                'run-ambiguous',
                'contract-v1',
                '2026-05-10T22:55:00+00:00',
                'researched_skip_ambiguous',
                'ambiguous_direction',
                NULL,
                NULL,
                NULL,
                '2026-05-10T22:55:00+00:00',
                '2026-05-10T22:40:00+00:00'
            );
            INSERT INTO research_runs (
                research_run_id,
                market_ticker,
                trigger_headline,
                trigger_source,
                attempted,
                summary,
                verdict_status,
                force_side,
                estimated_probability,
                confidence,
                market_price,
                estimated_edge,
                created_ts
            ) VALUES (
                'run-candidate',
                'KX-READY',
                'scheduled prewarm',
                'research_prewarm',
                1,
                'Research supports yes.',
                'decision_grade_candidate',
                'yes',
                0.63,
                0.71,
                0.51,
                0.11,
                '2026-05-10T22:40:00+00:00'
            );
            INSERT INTO research_evidence (
                market_ticker,
                research_run_id,
                contract_fingerprint,
                source_class,
                url,
                title,
                retrieved_at,
                inserted_at
            ) VALUES
            (
                'KX-READY',
                'run-candidate',
                'contract-v1',
                'resolution_source',
                'https://example.test/resolution',
                'Resolution',
                '2026-05-10T12:46:00+00:00',
                '2026-05-10T22:46:00+00:00'
            ),
            (
                'KX-READY',
                'run-candidate',
                'contract-v1',
                'corroborating_source',
                'https://example.test/corroboration',
                'Corroboration',
                '2026-05-10T12:47:00+00:00',
                '2026-05-10T22:47:00+00:00'
            );
            """
        )

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert assessment.matched_research_proofs == 0


def test_rollout_gate_rejects_mismatched_contract_fingerprint(tmp_path):
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
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
            },
        ],
    )
    _write_research_db(tmp_path, contract_fingerprint="contract-v2")

    assessment = evaluate_research_rollout(
        tmp_path,
        trades,
        bot_log=bot_log,
        expected_version="0.99.0",
        now=NOW,
    )

    assert not assessment.ok
    assert assessment.matched_research_proofs == 0
    assert any("no active market-eligible decision-grade proof" in item for item in assessment.failures)


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
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
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
                "research_contract_fingerprint": "contract-v1",
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
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
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
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
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
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-1",
                "research_contract_fingerprint": "contract-v1",
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
                "research_status": "decision_grade_candidate",
                "research_run_id": "run-2",
                "research_contract_fingerprint": "contract-v1",
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
        "no active market-eligible decision-grade proof" in item
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
