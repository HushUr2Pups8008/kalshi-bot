from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from scripts.research_multi_agent_workflow import (
    evaluate_research_multi_agent_workflow,
    main,
)
from tests._helpers import write_jsonl


NOW = datetime(2026, 6, 28, 23, 30, tzinfo=timezone.utc)


def _write_profile_and_env(repo_root, *, live_trading: str = "false") -> None:
    profile = repo_root / "profile.env"
    body = "\n".join(
        [
            "REAL_WEB_RESEARCH_MODE=shadow",
            "ENABLE_RESEARCH_PREWARM_TASK=true",
            f"LIVE_TRADING_ENABLED={live_trading}",
        ]
    )
    profile.write_text(body + "\n", encoding="utf-8")
    (repo_root / ".env").write_text(body + "\n", encoding="utf-8")


def _write_dossier_db(repo_root, *, retrieved_at: str = "2026-06-28T23:20:00Z") -> None:
    db_path = repo_root / "data" / "evidence_store.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
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
                last_confidence REAL
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
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                title TEXT NOT NULL,
                snippet TEXT NOT NULL,
                claim_type TEXT NOT NULL,
                supports_direction TEXT NOT NULL,
                supports_confidence REAL NOT NULL,
                retrieved_at TEXT,
                inserted_at TEXT,
                contract_fingerprint TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO research_dossiers VALUES (
                'KXGDP-26JUL30-T4.0',
                'rr-good',
                'fp-good',
                ?,
                'continue_researching',
                'missing_resolution_source',
                NULL,
                NULL,
                NULL
            )
            """,
            (retrieved_at,),
        )
        for index, source_class in enumerate(("official", "news"), start=1):
            conn.execute(
                """
                INSERT INTO research_evidence VALUES (
                    ?,
                    'KXGDP-26JUL30-T4.0',
                    'rr-good',
                    ?,
                    'source',
                    'https://example.com',
                    'title',
                    'snippet',
                    'settlement',
                    'neutral',
                    0.8,
                    ?,
                    ?,
                    'fp-good'
                )
                """,
                (f"ev-{index}", source_class, retrieved_at, retrieved_at),
            )


def _write_trade_log(path, records) -> None:
    write_jsonl(path, records)


def _write_bot_log(path, *, boot_ts: str = "2026-06-28 23:00:00,000 UTC") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{boot_ts} INFO kalshi_bot [BOOT] version=0.33.22 pid=123\n",
        encoding="utf-8",
    )


def test_research_multi_agent_workflow_passes_shadow_with_fresh_evidence(tmp_path):
    _write_profile_and_env(tmp_path)
    _write_dossier_db(tmp_path)
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    _write_trade_log(
        trades,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-28T23:20:00Z",
                "ticker": "KXGDP-26JUL30-T4.0",
                "research_status": "continue_researching",
                "research_skip_reason": "missing_resolution_source",
            },
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-28T23:20:01Z",
                "ticker": "KXCPI-26JUL-T0.3",
                "research_status": "continue_researching",
                "research_skip_reason": "missing_resolution_source",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-28T23:22:00Z",
                "ticker": "KXGDP-26JUL30-T4.0",
                "reason": "researched_no_edge",
                "research_status": "continue_researching",
            },
        ],
    )

    assessment = evaluate_research_multi_agent_workflow(
        tmp_path,
        trades,
        profile_path=tmp_path / "profile.env",
        now=NOW,
        window_hours=1,
    )

    assert assessment.ok
    assert {agent.name for agent in assessment.agents} == {
        "activation",
        "signal_flow",
        "prewarm_quality",
        "dossier_evidence",
        "capital_safety",
        "rollout_readiness",
    }


def test_research_multi_agent_workflow_fails_repeated_prewarm_spend(tmp_path):
    _write_profile_and_env(tmp_path)
    _write_dossier_db(tmp_path)
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    _write_trade_log(
        trades,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": f"2026-06-28T23:2{index}:00Z",
                "ticker": "KXREPEAT-26JUL30-T4.0",
                "research_status": "continue_researching",
            }
            for index in range(5)
        ],
    )

    assessment = evaluate_research_multi_agent_workflow(
        tmp_path,
        trades,
        profile_path=tmp_path / "profile.env",
        now=NOW,
        window_hours=1,
        max_prewarm_duplicate_ratio=0.5,
    )

    assert not assessment.ok
    prewarm = assessment.agent("prewarm_quality")
    assert not prewarm.ok
    assert "duplicate prewarm spend" in prewarm.findings[0]


def test_research_multi_agent_workflow_ignores_repeats_before_active_boot(tmp_path):
    _write_profile_and_env(tmp_path)
    _write_dossier_db(tmp_path)
    bot_log = tmp_path / "logs" / "app" / "bot.log"
    _write_bot_log(bot_log)
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    _write_trade_log(
        trades,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": f"2026-06-28T22:4{index}:00Z",
                "ticker": "KXOLDREPEAT-26JUL30-T4.0",
                "research_status": "continue_researching",
            }
            for index in range(5)
        ]
        + [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-28T23:20:00Z",
                "ticker": "KXGDP-26JUL30-T4.0",
                "research_status": "continue_researching",
            }
        ],
    )

    assessment = evaluate_research_multi_agent_workflow(
        tmp_path,
        trades,
        profile_path=tmp_path / "profile.env",
        bot_log=bot_log,
        now=NOW,
        window_hours=1,
    )

    assert assessment.ok
    prewarm = assessment.agent("prewarm_quality")
    assert prewarm.ok
    assert prewarm.metrics["prewarm_rows"] == 1
    assert prewarm.metrics["prewarm_window_since"] == "2026-06-28T23:00:00+00:00"


def test_research_multi_agent_workflow_fails_repeats_after_active_boot(tmp_path):
    _write_profile_and_env(tmp_path)
    _write_dossier_db(tmp_path)
    bot_log = tmp_path / "logs" / "app" / "bot.log"
    _write_bot_log(bot_log)
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    _write_trade_log(
        trades,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": f"2026-06-28T23:2{index}:00Z",
                "ticker": "KXREPEAT-26JUL30-T4.0",
                "research_status": "continue_researching",
            }
            for index in range(5)
        ],
    )

    assessment = evaluate_research_multi_agent_workflow(
        tmp_path,
        trades,
        profile_path=tmp_path / "profile.env",
        bot_log=bot_log,
        now=NOW,
        window_hours=1,
        max_prewarm_duplicate_ratio=0.5,
    )

    assert not assessment.ok
    prewarm = assessment.agent("prewarm_quality")
    assert not prewarm.ok
    assert "duplicate prewarm spend" in prewarm.findings[0]


def test_research_multi_agent_workflow_fails_when_live_orders_seen(tmp_path):
    _write_profile_and_env(tmp_path)
    _write_dossier_db(tmp_path)
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    _write_trade_log(
        trades,
        [
            {
                "type": "RESEARCH_PREWARM_RESULT",
                "ts": "2026-06-28T23:20:00Z",
                "ticker": "KXGDP-26JUL30-T4.0",
                "research_status": "continue_researching",
            },
            {
                "type": "LIVE_ORDER",
                "ts": "2026-06-28T23:21:00Z",
                "ticker": "KXGDP-26JUL30-T4.0",
            },
        ],
    )

    assessment = evaluate_research_multi_agent_workflow(
        tmp_path,
        trades,
        profile_path=tmp_path / "profile.env",
        now=NOW,
        window_hours=1,
    )

    assert not assessment.ok
    capital = assessment.agent("capital_safety")
    assert not capital.ok
    assert capital.findings == ["LIVE_ORDER rows observed in research workflow window: 1"]


def test_research_multi_agent_workflow_json_cli_exits_nonzero_on_failure(
    capsys,
    tmp_path,
):
    _write_profile_and_env(tmp_path, live_trading="true")
    trades = tmp_path / "logs" / "trades" / "live" / "trades.jsonl"
    _write_trade_log(trades, [])

    code = main(
        [
            "--home",
            str(tmp_path),
            "--trades-log",
            str(trades),
            "--profile",
            str(tmp_path / "profile.env"),
            "--now",
            NOW.isoformat(),
            "--window-hours",
            "1",
            "--json",
        ]
    )

    assert code == 1
    out = capsys.readouterr().out
    assert '"ok": false' in out
    assert "LIVE_TRADING_ENABLED=true is not allowed" in out
