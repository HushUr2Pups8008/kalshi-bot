from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.research_profit_validation_loop import (
    _latest_boot_version,
    _log_error_samples,
    _load_candidate_proofs,
    evaluate_research_profit_validation,
    render_markdown,
)
from tests._helpers import write_jsonl


NOW = datetime(2026, 6, 29, 12, tzinfo=timezone.utc)


def _write_trade_log(path: Path, records: list[dict]) -> None:
    write_jsonl(path, records)


def _write_evidence_store(
    path: Path,
    *,
    ticker: str = "KXPROFIT-26JUL01",
    verdict_status: str = "decision_grade_candidate",
    skip_reason: str | None = None,
    market_price: float | None = 0.51,
    estimated_edge: float | None = 0.12,
    market_status: str | None = "active",
    market_close_time: str | None = "2026-07-01T00:00:00Z",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE research_runs (
                research_run_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                trigger_headline TEXT,
                trigger_source TEXT,
                market_status TEXT,
                market_close_time TEXT,
                attempted INTEGER,
                summary TEXT,
                verdict_status TEXT,
                skip_reason TEXT,
                force_side TEXT,
                estimated_probability REAL,
                confidence REAL,
                market_price REAL,
                estimated_edge REAL,
                decision_grade_status TEXT,
                created_ts TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE research_evidence (
                evidence_id TEXT PRIMARY KEY,
                market_ticker TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                source_class TEXT NOT NULL,
                source_name TEXT,
                source_url TEXT,
                title TEXT,
                snippet TEXT,
                claim_type TEXT,
                supports_direction TEXT,
                supports_confidence REAL,
                retrieved_at TEXT,
                inserted_at TEXT,
                contract_fingerprint TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE research_run_queries (
                research_run_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                query TEXT NOT NULL,
                query_intent TEXT NOT NULL,
                source_class TEXT NOT NULL,
                PRIMARY KEY (research_run_id, ordinal)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE research_dossiers (
                market_ticker TEXT PRIMARY KEY,
                last_research_run_id TEXT,
                market_status TEXT,
                market_close_time TEXT,
                last_researched_ts TEXT,
                last_verdict_status TEXT,
                last_skip_reason TEXT,
                last_force_side TEXT,
                last_estimated_probability REAL,
                last_confidence REAL,
                last_market_price REAL,
                last_estimated_edge REAL,
                last_decision_grade_status TEXT,
                created_ts TEXT,
                updated_ts TEXT,
                last_contract_fingerprint TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_runs VALUES (
                'rr-profitable',
                ?,
                'headline',
                'source',
                ?,
                ?,
                1,
                'summary',
                ?,
                ?,
                'yes',
                0.64,
                0.74,
                ?,
                ?,
                ?,
                '2026-06-29T10:00:00Z'
            )
            """,
            (
                ticker,
                market_status,
                market_close_time,
                verdict_status,
                skip_reason,
                market_price,
                estimated_edge,
                (
                    verdict_status
                    if verdict_status == "decision_grade_candidate"
                    else None
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO research_dossiers VALUES (
                ?,
                'rr-profitable',
                ?,
                ?,
                '2026-06-29T10:00:00Z',
                ?,
                ?,
                'yes',
                0.64,
                0.74,
                ?,
                ?,
                ?,
                '2026-06-29T10:00:00Z',
                '2026-06-29T10:00:00Z',
                'fp-profitable'
            )
            """,
            (
                ticker,
                market_status,
                market_close_time,
                verdict_status,
                skip_reason,
                market_price,
                estimated_edge,
                (
                    verdict_status
                    if verdict_status == "decision_grade_candidate"
                    else None
                ),
            ),
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
                INSERT INTO research_run_queries VALUES (
                    'rr-profitable', ?, ?, ?, ?
                )
                """,
                (
                    ordinal,
                    f"KXPROFIT-26JUL01 {query_intent}",
                    query_intent,
                    source_class,
                ),
            )
        evidence_rows = (
            (
                "resolution_source",
                "settlement",
                "yes",
                "https://official.test/final-result",
            ),
            (
                "rules_source",
                "rules",
                "yes",
                "https://rules.test/contract-terms",
            ),
            (
                "reputable_secondary",
                "disconfirming",
                "no",
                "https://apnews.test/counter-report",
            ),
        )
        for index, (
            source_class,
            claim_type,
            supports_direction,
            source_url,
        ) in enumerate(evidence_rows, start=1):
            conn.execute(
                """
                INSERT INTO research_evidence VALUES (
                    ?,
                    ?,
                    'rr-profitable',
                    ?,
                    'source',
                    ?,
                    'title',
                    'snippet',
                    ?,
                    ?,
                    0.8,
                    '2026-06-29T10:00:00Z',
                    '2026-06-29T10:00:00Z',
                    'fp-profitable'
                )
                """,
                (
                    f"ev-{index}",
                    ticker,
                    source_class,
                    source_url,
                    claim_type,
                    supports_direction,
                ),
            )


def _write_paper_db(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY,
                ts TEXT,
                ticker TEXT,
                side TEXT,
                contracts INTEGER,
                price_cents INTEGER,
                cost_dollars REAL,
                estimated_prob REAL,
                edge REAL,
                resolved INTEGER,
                resolved_yes INTEGER,
                pnl_dollars REAL,
                notional_bankroll_before REAL,
                notional_bankroll_after REAL,
                resolved_ts TEXT
            )
            """
        )
        for row in rows:
            conn.execute(
                """
                INSERT INTO paper_trades VALUES (
                    :trade_id,
                    :ts,
                    :ticker,
                    'yes',
                    1,
                    50,
                    :cost_dollars,
                    0.64,
                    :edge,
                    :resolved,
                    :resolved_yes,
                    :pnl_dollars,
                    :notional_bankroll_before,
                    :notional_bankroll_after,
                    :resolved_ts
                )
                """,
                row,
            )


def _paper_row(
    trade_id: str,
    *,
    ticker: str = "KXPROFIT-26JUL01",
    pnl: float,
    before: float = 100.0,
) -> dict:
    return {
        "trade_id": trade_id,
        "ts": "2026-06-29T11:00:00Z",
        "ticker": ticker,
        "cost_dollars": 0.50,
        "edge": 0.09,
        "resolved": 1,
        "resolved_yes": 1 if pnl > 0 else 0,
        "pnl_dollars": pnl,
        "notional_bankroll_before": before,
        "notional_bankroll_after": before + pnl,
        "resolved_ts": "2026-06-29T11:30:00Z",
    }


def _base_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    trades_log = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    evidence_db = tmp_path / "data" / "evidence_store.db"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_evidence_store(evidence_db)
    return trades_log, evidence_db, paper_db


def test_research_gate_health_without_trades_continues_shadow_cleanly(tmp_path: Path) -> None:
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

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.verdict == "PROVISIONALLY_SUCCESSFUL"
    assert report.action == "CONTINUE_SHADOW"
    assert "no research-backed trades in current window" in report.reasons
    assert report.funnel.research_backed_opportunities == 1
    assert report.funnel.research_backed_trades == 0
    assert report.runtime.botcheck_summary == []
    assert report.profit.unrealized_pnl is None
    assert report.profit.unrealized_pnl_unavailable_reason == (
        "open-position mark prices unavailable"
    )
    assert report.changes.recent_commits == []
    assert report.changes.remotes_synced is None


def test_untradeable_terminal_research_does_not_require_live_cache_candidate(
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

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.verdict == "PROVISIONALLY_SUCCESSFUL"
    assert report.action == "CONTINUE_SHADOW"
    assert report.research_operating_cleanly is True
    assert report.research_supports_trades is False
    assert report.decision_grade.decision_grade_candidates == 0
    assert report.decision_grade.terminal_untradeable == 1
    assert "no decision_grade_candidate evidence" not in report.reasons
    assert "no live_cache_eligible research proof" not in report.reasons
    assert any("terminal no-trade" in reason for reason in report.reasons)


def test_insufficient_untradeable_research_does_not_satisfy_decision_grade(
    tmp_path: Path,
) -> None:
    trades_log = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    evidence_db = tmp_path / "data" / "evidence_store.db"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_evidence_store(
        evidence_db,
        verdict_status="untradeable",
        skip_reason="no_reliable_source_path",
    )
    _write_trade_log(
        trades_log,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-29T10:01:00Z",
                "ticker": "KXPROFIT-26JUL01",
                "research_status": "untradeable",
                "research_skip_reason": "no_reliable_source_path",
                "research_run_id": "rr-profitable",
            },
        ],
    )
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.decision_grade.terminal_untradeable == 0
    assert "no decision_grade_candidate evidence" in report.reasons


def test_terminal_no_source_task_reports_as_stale_but_researchable(
    tmp_path: Path,
) -> None:
    trades_log = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    evidence_db = tmp_path / "data" / "evidence_store.db"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_evidence_store(
        evidence_db,
        verdict_status="needs_research",
        skip_reason="missing_resolution_source",
    )
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
            INSERT INTO research_tasks (
                market_ticker, state, updated_ts, terminal_reason
            ) VALUES (
                'KXPROFIT-26JUL01',
                'untradeable',
                '2026-06-29T10:10:00Z',
                'no_reliable_source_path'
            )
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.decision_grade.terminal_untradeable == 0
    assert report.decision_grade.blocked_by_no_reliable_source_path == 0
    assert report.decision_grade.stale_but_researchable == 1


def test_decision_grade_report_separates_trade_support_from_operational_health(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        rows = [
            ("rr-missing-price", "needs_price_edge", "missing_market_price"),
            ("rr-no-source", "needs_research", "missing_resolution_source"),
            ("rr-official-pending", "needs_research", "official_data_pending"),
            ("rr-neutral", "needs_counter_evidence", "neutral_only_evidence"),
            ("rr-no-counter", "needs_counter_evidence", "missing_counter_evidence"),
            ("rr-generic", "needs_counter_evidence", "generic_summary"),
            (
                "rr-contradiction",
                "needs_counter_evidence",
                "unresolved_contradiction",
            ),
            ("rr-stale", "needs_research", "source_freshness_ttl_exceeded"),
            ("rr-provider", "research_provider_error", "research_provider_error"),
        ]
        for index, (run_id, status, skip_reason) in enumerate(rows, start=1):
            conn.execute(
                """
                INSERT INTO research_runs (
                    research_run_id, market_ticker, trigger_headline,
                    trigger_source, attempted, summary, verdict_status,
                    skip_reason, force_side, estimated_probability, confidence,
                    market_price, estimated_edge, decision_grade_status, created_ts
                ) VALUES (?, ?, 'headline', 'source', 1,
                    'summary', ?, ?, 'yes', 0.64, 0.74, 0.51, 0.11,
                    CASE WHEN ? = 'decision_grade_candidate' THEN ? ELSE NULL END,
                    '2026-06-29T10:00:00Z')
                """,
                (
                    run_id,
                    f"KXBLOCKER-26JUL01-{index}",
                    status,
                    skip_reason,
                    status,
                    status,
                ),
            )
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
                'KXUNTRADEABLE-26JUL01',
                'untradeable',
                '2026-06-29T10:00:00Z',
                'no_edge'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_tasks VALUES (
                'KXTIMEOUT-26JUL01',
                'untradeable',
                '2026-06-29T10:00:00Z',
                'research_timeout_exhausted'
            )
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
                "research_timeout_stage": "provider_fanout",
                "research_provider_error_count": 3,
            }
        ],
    )
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )
    rendered = render_markdown(report)

    assert report.decision_grade.decision_grade_candidates == 1
    assert report.decision_grade.blocked_by_missing_price == 1
    assert report.decision_grade.blocked_by_no_reliable_source_path == 1
    assert report.decision_grade.blocked_by_official_data_pending == 1
    assert report.decision_grade.blocked_by_provider_error == 1
    assert report.decision_grade.blocked_by_neutral_evidence == 1
    assert report.decision_grade.blocked_by_no_counter_evidence == 1
    assert report.decision_grade.blocked_by_generic_summary == 1
    assert report.decision_grade.blocked_by_unresolved_contradiction == 1
    assert report.decision_grade.stale_but_researchable == 1
    assert report.decision_grade.terminal_untradeable == 1
    assert report.decision_grade.terminal_timeout_exhausted == 1
    assert report.funnel.trade_candidates == 1
    assert report.runtime.research_timeout_stage_counts == {"provider_fanout": 1}
    assert report.runtime.research_provider_error_count == 3
    assert "- decision-grade candidates: 1" in rendered
    assert "- blocked by missing price: 1" in rendered
    assert "- blocked by no reliable source path: 1" in rendered
    assert "- blocked by official data pending: 1" in rendered
    assert "- blocked by provider error: 1" in rendered
    assert "- blocked by unresolved contradiction: 1" in rendered
    assert "- terminal timeout exhausted: 1" in rendered
    assert "- research timeout stages: {'provider_fanout': 1}" in rendered
    assert "- research provider errors: 3" in rendered
    assert any("no reliable source path" in reason for reason in report.reasons)
    assert any("counter-evidence" in reason for reason in report.reasons)
    assert any("official settlement data is pending" in reason for reason in report.reasons)


def test_official_data_pending_is_reported_as_shadow_wait_reason(tmp_path: Path) -> None:
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
                'Official settlement source has not published yet.',
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
                'KXPROTECTED-26JUL01',
                'untradeable',
                '2026-06-29T10:00:00Z',
                'no_edge'
            )
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.decision_grade.blocked_by_official_data_pending == 1
    assert report.verdict == "PROVISIONALLY_SUCCESSFUL"
    assert report.action == "CONTINUE_SHADOW"
    assert any("official settlement data is pending" in reason for reason in report.reasons)


def test_candidate_proofs_include_decision_grade_candidates(tmp_path: Path) -> None:
    evidence_db = tmp_path / "proofs" / "evidence_store.db"
    _write_evidence_store(evidence_db)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
            UPDATE research_runs
            SET verdict_status = 'decision_grade_candidate'
            WHERE research_run_id = 'rr-profitable'
            """
        )

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    proof = proofs["KXPROFIT-26JUL01"]
    assert proof.research_run_id == "rr-profitable"
    assert proof.live_cache_eligible


def test_candidate_proofs_reject_counter_query_boilerplate_match(tmp_path: Path) -> None:
    evidence_db = tmp_path / "proofs" / "evidence_store.db"
    ticker = "KXUSTRDAGREEMENT-26JUL01"
    question = "Will the US sign a trade agreement before July 1?"
    _write_evidence_store(evidence_db, ticker=ticker)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
            UPDATE research_run_queries
            SET query = CASE
                WHEN query_intent = 'disconfirming'
                THEN ?
                ELSE ?
            END
            """,
            (
                (
                    f"{question} evidence against YES evidence against NO false not "
                    "confirmed denied opponent objection"
                ),
                question,
            ),
        )
        conn.execute(
            """
            UPDATE research_evidence
            SET title = 'US signs bilateral trade agreement',
                snippet = 'Officials signed the trade agreement before July 1.'
            WHERE claim_type = 'settlement'
            """
        )
        conn.execute(
            """
            UPDATE research_evidence
            SET title = 'Opponent denied objection',
                snippet = 'The objection concerns an unrelated sports dispute.'
            WHERE claim_type = 'disconfirming'
            """
        )

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}


@pytest.mark.parametrize(
    ("market_status", "market_close_time"),
    [
        ("active", "2026-06-29T12:00:00Z"),
        ("closed", "2026-07-01T00:00:00Z"),
        (None, "2026-07-01T00:00:00Z"),
        ("active", None),
    ],
)
def test_candidate_proofs_fail_closed_when_latest_market_is_ineligible(
    tmp_path: Path,
    market_status: str | None,
    market_close_time: str | None,
) -> None:
    evidence_db = tmp_path / "data" / "evidence_store.db"
    _write_evidence_store(
        evidence_db,
        market_status=market_status,
        market_close_time=market_close_time,
    )

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}


def test_market_ineligible_candidate_is_reported_as_explicit_blocker(
    tmp_path: Path,
) -> None:
    trades_log = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    evidence_db = tmp_path / "data" / "evidence_store.db"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_evidence_store(
        evidence_db,
        market_status="active",
        market_close_time="2026-06-29T12:00:00Z",
    )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.decision_grade.decision_grade_candidates == 0
    assert report.decision_grade.blocked_by_market_ineligible == 1
    assert report.funnel.live_cache_eligible == 0
    assert "- blocked by market ineligible: 1" in render_markdown(report)
    assert any("inactive, expired, or unverified market" in reason for reason in report.reasons)


def test_candidate_proofs_fail_closed_when_eligibility_schema_is_missing(
    tmp_path: Path,
) -> None:
    evidence_db = tmp_path / "data" / "evidence_store.db"
    _write_evidence_store(evidence_db)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute("ALTER TABLE research_dossiers DROP COLUMN market_status")
        conn.execute("ALTER TABLE research_dossiers DROP COLUMN market_close_time")

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}


def test_candidate_proofs_require_source_class_diversity(tmp_path: Path) -> None:
    evidence_db = tmp_path / "data" / "evidence_store.db"
    _write_evidence_store(evidence_db)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            "UPDATE research_evidence SET source_class = 'official_primary'"
        )

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}


def test_candidate_proofs_accept_structured_official_metric(tmp_path: Path) -> None:
    evidence_db = tmp_path / "proofs" / "evidence_store.db"
    _write_evidence_store(evidence_db, ticker="KXHIGHNY-26JUN28-T99")
    with sqlite3.connect(evidence_db) as conn:
        conn.execute("ALTER TABLE research_evidence ADD COLUMN metric_name TEXT")
        conn.execute("ALTER TABLE research_evidence ADD COLUMN published_at TEXT")
        conn.execute(
            """
            UPDATE research_runs
            SET verdict_status = 'decision_grade_candidate',
                force_side = 'yes',
                estimated_probability = 0.94,
                confidence = 0.9,
                market_price = 0.06,
                estimated_edge = 0.87,
                decision_grade_status = 'decision_grade_candidate'
            WHERE research_run_id = 'rr-profitable'
            """
        )
        conn.execute("DELETE FROM research_evidence")
        conn.executemany(
            """
            INSERT INTO research_evidence (
                evidence_id, market_ticker, research_run_id, source_class,
                source_name, source_url, title, snippet, claim_type,
                supports_direction, supports_confidence, retrieved_at,
                inserted_at, contract_fingerprint, metric_name, published_at
            )
            VALUES (?, ?, 'rr-profitable', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "ev-structured-nws",
                    "KXHIGHNY-26JUN28-T99",
                    "official_primary",
                    "NWS Climatological Report",
                    "https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
                    "NWS Central Park daily maximum for June 28, 2026: 93F",
                    "NWS Central Park climate report lists TODAY MAXIMUM 93F.",
                    "official_resolution",
                    "yes",
                    0.95,
                    "2026-06-29T10:00:00Z",
                    "2026-06-29T10:00:00Z",
                    "fp-profitable",
                    "nws_daily_high_temp_f",
                    "2026-06-28",
                ),
                (
                    "ev-counter",
                    "KXHIGHNY-26JUN28-T99",
                    "reputable_secondary",
                    "Independent Weather Archive",
                    "https://weather.example.com/nyc-counter",
                    "NYC daily high countercheck",
                    "Independent countercheck found no higher official reading.",
                    "disconfirming",
                    "neutral",
                    0.65,
                    "2026-06-29T10:00:00Z",
                    "2026-06-29T10:00:00Z",
                    "fp-profitable",
                    None,
                    None,
                ),
            ],
        )

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    proof = proofs["KXHIGHNY-26JUN28-T99"]
    assert proof.research_run_id == "rr-profitable"
    assert proof.live_cache_eligible


def test_candidate_proofs_reject_same_source_rewrites(tmp_path: Path) -> None:
    evidence_db = tmp_path / "proofs" / "evidence_store.db"
    _write_evidence_store(evidence_db)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
            UPDATE research_runs
            SET verdict_status = 'decision_grade_candidate'
            WHERE research_run_id = 'rr-profitable'
            """
        )
        conn.execute(
            """
            UPDATE research_evidence
            SET source_name = 'Wire rewrite',
                source_url = 'https://wire.example.com/story'
            """
        )

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}


def test_candidate_proofs_require_directional_support_for_speech_resolution_phrase(
    tmp_path: Path,
) -> None:
    evidence_db = tmp_path / "proofs" / "evidence_store.db"
    ticker = "KXTRUMPMENTION-26JUL24-MAGA"
    _write_evidence_store(evidence_db, ticker=ticker)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
            UPDATE research_run_queries
            SET query = ?
            WHERE research_run_id = 'rr-profitable'
              AND query_intent = 'official_resolution'
            """,
            (
                "What will Donald Trump say during the rescheduled dinner? "
                "If Donald Trump says MAGA / Make America Great Again as part "
                "of the dinner, then the market resolves Yes. official resolution latest",
            ),
        )
        conn.execute(
            """
            UPDATE research_evidence
            SET title = CASE evidence_id
                    WHEN 'ev-1' THEN 'Celebrations start in DC for America birthday'
                    WHEN 'ev-2' THEN 'Contract terms for the rescheduled dinner'
                    ELSE 'No MAGA wording confirmed for the dinner'
                END,
                snippet = CASE evidence_id
                    WHEN 'ev-1' THEN 'Officials expect tight security at the White House event.'
                    WHEN 'ev-2' THEN 'Contract rules define the mention condition.'
                    ELSE 'No direct evidence confirms Trump will say MAGA at the event.'
                END,
                supports_direction = CASE evidence_id
                    WHEN 'ev-1' THEN 'yes'
                    WHEN 'ev-2' THEN 'neutral'
                    ELSE 'no'
                END
            """
        )

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}


def test_candidate_proofs_reject_counter_evidence_without_disconfirming_query(
    tmp_path: Path,
) -> None:
    evidence_db = tmp_path / "proofs" / "evidence_store.db"
    _write_evidence_store(evidence_db)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
            UPDATE research_runs
            SET verdict_status = 'decision_grade_candidate'
            WHERE research_run_id = 'rr-profitable'
            """
        )
        conn.execute(
            """
            DELETE FROM research_run_queries
            WHERE research_run_id = 'rr-profitable'
              AND query_intent = 'disconfirming'
            """
        )

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}


def test_candidate_proofs_ignore_legacy_trade_candidates_without_price_edge(
    tmp_path: Path,
) -> None:
    evidence_db = tmp_path / "proofs" / "evidence_store.db"
    _write_evidence_store(
        evidence_db,
        verdict_status="trade_candidate",
        market_price=None,
        estimated_edge=None,
    )

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}


def test_candidate_proofs_reject_decision_grade_candidate_with_bad_edge(
    tmp_path: Path,
) -> None:
    evidence_db = tmp_path / "proofs" / "evidence_store.db"
    _write_evidence_store(evidence_db, estimated_edge=0.99)

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}


def test_decision_grade_report_rejects_candidate_with_only_neutral_evidence(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute("UPDATE research_evidence SET supports_direction = 'neutral'")
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )
    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}
    assert report.decision_grade.decision_grade_candidates == 0
    assert report.decision_grade.blocked_by_neutral_evidence == 1


def test_decision_grade_report_rejects_candidate_without_counter_evidence(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
            UPDATE research_evidence
            SET claim_type = 'settlement',
                supports_direction = 'yes'
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )
    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}
    assert report.decision_grade.decision_grade_candidates == 0
    assert report.decision_grade.blocked_by_no_counter_evidence == 1
    assert any(
        "research_decision_grade_repair.py --apply" in reason
        for reason in report.reasons
    )
    assert any(
        "research_decision_grade_repair.py --apply" in reason
        for reason in report.reasons
    )


def test_ambiguous_direction_reports_separate_decision_blocker(
    tmp_path: Path,
) -> None:
    trades_log = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    evidence_db = tmp_path / "data" / "evidence_store.db"
    paper_db = tmp_path / "data" / "paper_trades.db"
    _write_evidence_store(
        evidence_db,
        verdict_status="needs_counter_evidence",
        skip_reason="ambiguous_direction",
    )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.decision_grade.blocked_by_ambiguous_direction == 1
    assert report.decision_grade.blocked_by_no_counter_evidence == 0
    assert any("directional probability" in reason for reason in report.reasons)


def test_missing_counter_evidence_continues_shadow_when_capital_protected(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute(
            """
            UPDATE research_evidence
            SET claim_type = 'settlement',
                supports_direction = 'yes'
            """
        )
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
                'KXPROTECTED-26JUL01',
                'untradeable',
                '2026-06-29T10:00:00Z',
                'no_edge'
            )
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.decision_grade.blocked_by_no_counter_evidence == 1
    assert report.decision_grade.terminal_untradeable == 1
    assert report.verdict == "PROVISIONALLY_SUCCESSFUL"
    assert report.action == "CONTINUE_SHADOW"
    assert any("counter-evidence" in reason for reason in report.reasons)


def test_insufficient_directional_evidence_terminal_counts_as_no_trade(
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
                'KXAMBIGUOUS-26JUL01',
                'untradeable',
                '2026-06-29T10:00:00Z',
                'insufficient_directional_evidence'
            )
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.decision_grade.terminal_untradeable == 1


def test_decision_grade_report_ignores_superseded_blocked_runs(
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
            ) VALUES ('rr-old-missing-source', 'KXPROFIT-26JUL01', 'headline',
                'source', 1, 'summary', 'needs_research',
                'missing_resolution_source', 'yes', 0.64, 0.74, 0.51, 0.11,
                NULL, '2026-06-29T09:00:00Z')
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

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.decision_grade.decision_grade_candidates == 1
    assert report.decision_grade.blocked_by_no_reliable_source_path == 0


def test_decision_grade_report_does_not_terminalize_nonterminal_timeouts(
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
            ) VALUES ('rr-timeout', 'KXTIMEOUT-26JUL01', 'headline',
                'source', 1, 'summary', 'continue_researching',
                'research_timeout', 'yes', 0.64, 0.74, 0.51, 0.11,
                NULL, '2026-06-29T10:00:00Z')
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.decision_grade.terminal_timeout_exhausted == 0


def test_decision_grade_report_rejects_same_side_disconfirming_evidence(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute("ALTER TABLE research_evidence ADD COLUMN metric_name TEXT")
        conn.execute(
            """
            UPDATE research_evidence
            SET source_class = 'official_primary',
                supports_direction = 'yes',
                supports_confidence = 0.95,
                metric_name = 'nws_daily_high_temp_f'
            WHERE claim_type = 'disconfirming'
            """
        )
    _write_trade_log(trades_log, [])
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )
    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}
    assert report.decision_grade.decision_grade_candidates == 0
    assert report.decision_grade.blocked_by_no_counter_evidence == 1


def test_profitable_research_backed_trades_are_strongly_successful(tmp_path: Path) -> None:
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
            {
                "type": "PAPER_TRADE",
                "ts": "2026-06-29T11:00:00Z",
                "ticker": "KXPROFIT-26JUL01",
                "edge": 0.09,
                "cost_dollars": 0.50,
            },
        ],
    )
    _write_paper_db(
        paper_db,
        [_paper_row(f"trade-{index}", pnl=0.20, before=100.0 + index * 0.2) for index in range(6)],
    )

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.verdict == "STRONGLY_SUCCESSFUL"
    assert report.action == "KEEP_PAPER_RUNNING"
    assert report.profit.net_pnl == pytest.approx(1.20)
    assert report.profit.profit_factor == float("inf")
    assert report.profit.roi_on_deployed_capital == pytest.approx(0.4)


def test_negative_research_backed_pnl_fails_closed(tmp_path: Path) -> None:
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
                "type": "PAPER_TRADE",
                "ts": "2026-06-29T11:00:00Z",
                "ticker": "KXPROFIT-26JUL01",
                "edge": 0.09,
                "cost_dollars": 0.50,
            },
        ],
    )
    _write_paper_db(paper_db, [_paper_row("loss", pnl=-0.40)])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.verdict == "NOT_SUCCESSFUL"
    assert report.action == "ROLL_BACK_OR_PATCH"
    assert "research-backed P&L is negative" in report.reasons


def test_positive_shadow_replay_supports_paper_review_without_trade_success(
    tmp_path: Path,
) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    replay_root = tmp_path / "logs" / "edge_replay"
    replay_root.mkdir(parents=True)
    (replay_root / "counterfactual_scores.json").write_text(
        json.dumps(
            {
                "trade_count": 30,
                "win_rate": 0.6,
                "realized_pnl": 6.0,
                "per_trade_ev": 0.2,
                "ev_ci_95_lo": 0.04,
                "ev_ci_95_hi": 0.35,
            }
        ),
        encoding="utf-8",
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

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        replay_root=replay_root,
        now=NOW,
    )

    assert report.verdict == "PROVISIONALLY_SUCCESSFUL"
    assert report.action == "PROMOTE_TO_PAPER_REVIEW"
    assert report.research_operating_cleanly is True
    assert report.research_supports_trades is True
    assert report.replay.best_per_trade_ev == pytest.approx(0.2)


def test_live_order_without_authorization_is_hard_fail(tmp_path: Path) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    _write_trade_log(
        trades_log,
        [
            {
                "type": "LIVE_ORDER",
                "ts": "2026-06-29T11:00:00Z",
                "ticker": "KXPROFIT-26JUL01",
            }
        ],
    )
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.verdict == "NOT_SUCCESSFUL"
    assert report.action == "ROLL_BACK_OR_PATCH"
    assert "unauthorized LIVE_ORDER count 1" in report.reasons


def test_within_cooldown_repeat_is_hard_fail(tmp_path: Path) -> None:
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
                "within_cooldown_repeat": True,
            }
        ],
    )
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.verdict == "NOT_SUCCESSFUL"
    assert report.action == "ROLL_BACK_OR_PATCH"
    assert report.runtime.within_cooldown_repeats == 1
    assert "within_cooldown_repeats 1" in report.reasons


def test_bothealth_red_requires_capital_safe_explanation(tmp_path: Path) -> None:
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
            }
        ],
    )
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
        bothealth_output="Verdict: **RED** — unknown order persistence failure",
    )

    assert report.verdict == "NOT_SUCCESSFUL"
    assert report.action == "ROLL_BACK_OR_PATCH"
    assert report.workflow.bothealth_verdict == "RED"
    assert "bothealth RED without capital-safe explanation" in report.reasons


def test_bothealth_red_post_fix_sample_size_is_capital_safe(tmp_path: Path) -> None:
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
            }
        ],
    )
    _write_paper_db(paper_db, [])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
        bothealth_output=(
            "Verdict: **RED** — POST_FIX_NEW readiness NOT_READY "
            "(post-clean-start production-proxy-complete rows 13 < min_trades 200)"
        ),
    )

    assert report.workflow.bothealth_verdict == "RED"
    assert report.workflow.bothealth_capital_safe is True
    assert "bothealth RED without capital-safe explanation" not in report.reasons


def test_conversion_rates_are_benchmarked_to_7d_and_30d(tmp_path: Path) -> None:
    trades_log, evidence_db, paper_db = _base_paths(tmp_path)
    _write_trade_log(
        trades_log,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-20T10:00:00Z",
                "ticker": "KXPROFIT-26JUL01",
                "research_status": "decision_grade_candidate",
                "research_run_id": "rr-profitable",
            },
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-28T10:00:00Z",
                "ticker": "KXPROFIT-26JUL01",
                "research_status": "decision_grade_candidate",
                "research_run_id": "rr-profitable",
            },
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-29T10:00:00Z",
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
            {
                "type": "PAPER_TRADE",
                "ts": "2026-06-29T11:00:00Z",
                "ticker": "KXPROFIT-26JUL01",
                "edge": 0.09,
                "cost_dollars": 0.50,
            },
        ],
    )
    _write_paper_db(paper_db, [_paper_row("win", pnl=0.20)])

    report = evaluate_research_profit_validation(
        tmp_path,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        now=NOW,
    )

    assert report.funnel.candidate_to_opportunity_rate == pytest.approx(1.0)
    assert report.funnel.seven_day_candidate_to_opportunity_rate == pytest.approx(0.5)
    assert report.funnel.thirty_day_candidate_to_opportunity_rate == pytest.approx(1 / 3)
    assert report.funnel.candidate_to_opportunity_delta_vs_7d == pytest.approx(0.5)
    assert report.funnel.candidate_to_opportunity_delta_vs_30d == pytest.approx(2 / 3)


def test_latest_boot_version_prefers_most_recent_boot(tmp_path: Path) -> None:
    app_log = tmp_path / "logs" / "app" / "bot.log"
    app_log.parent.mkdir(parents=True)
    app_log.write_text(
        "\n".join(
            [
                "2026-06-29 10:00:00,000 UTC INFO kalshi_bot [BOOT] version=0.33.21 pid=1",
                "2026-06-29 11:00:00,000 UTC INFO kalshi_bot [BOOT] version=0.33.22 pid=2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert _latest_boot_version(app_log) == "0.33.22"


def test_latest_boot_version_falls_back_to_repo_version_file(tmp_path: Path) -> None:
    app_log = tmp_path / "logs" / "app" / "bot.log"
    app_log.parent.mkdir(parents=True)
    app_log.write_text("", encoding="utf-8")
    (tmp_path / "VERSION").write_text("0.33.99\n", encoding="utf-8")

    assert _latest_boot_version(app_log) == "0.33.99"


def test_log_error_samples_ignore_legacy_transient_kalshi_503(
    tmp_path: Path,
) -> None:
    app_log = tmp_path / "logs" / "app" / "bot.log"
    app_log.parent.mkdir(parents=True)
    app_log.write_text(
        "\n".join(
            [
                (
                    "2026-06-29 10:00:00,000 UTC ERROR    kalshi_rest          "
                    "Request error GET /series/KXGDP: HTTPSConnectionPool("
                    "host='api.elections.kalshi.com', port=443): Max retries "
                    "exceeded with url: /trade-api/v2/series/KXGDP (Caused by "
                    "ResponseError('too many 503 error responses'))"
                ),
                (
                    "2026-06-29 10:01:00,000 UTC ERROR    main                 "
                    "unexpected research persistence failure"
                ),
            ]
        ),
        encoding="utf-8",
    )

    samples = _log_error_samples(
        app_log,
        since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
    )

    assert samples == [
        "2026-06-29 10:01:00,000 UTC ERROR    main                 "
        "unexpected research persistence failure"
    ]
def test_candidate_proofs_reject_future_nws_evidence(tmp_path: Path) -> None:
    evidence_db = tmp_path / "data" / "evidence_store.db"
    _write_evidence_store(evidence_db)
    with sqlite3.connect(evidence_db) as conn:
        conn.execute("ALTER TABLE research_evidence ADD COLUMN metric_name TEXT")
        conn.execute("ALTER TABLE research_evidence ADD COLUMN published_at TEXT")
        conn.execute("ALTER TABLE research_evidence ADD COLUMN raw_payload_json TEXT")
        conn.execute(
            """
            UPDATE research_evidence
            SET metric_name = 'nws_daily_high_temp_f',
                published_at = '2026-06-30',
                raw_payload_json = '{}'
            WHERE claim_type = 'settlement'
            """
        )

    proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=datetime(2026, 6, 29, 0, tzinfo=timezone.utc),
        now=NOW,
    )

    assert proofs == {}
