from __future__ import annotations

import pytest

from tests._helpers import write_jsonl


def _load_module():
    try:
        import scripts.since_restart_money_path as module
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing scripts.since_restart_money_path module: {exc}")
    return module


def test_build_report_joins_candidate_chain_and_preserves_g6_fields(tmp_path):
    module = _load_module()
    log_path = tmp_path / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-18T01:00:00Z",
                "ticker": "KXCHAIN",
                "source": "AP News",
                "edge": 0.12,
            },
            {
                "type": "GATE_SUMMARY",
                "ts": "2026-06-18T01:00:04Z",
                "market_ticker": "KXCHAIN",
                "binding_constraint": "passed",
                "gate_chain": [
                    "G4: rc=0.2201 >= 0.2000 PASS",
                    "G1: sc=0.1385 >= 0.0500 PASS",
                    "G3: PASS",
                    "G6_recency_score: FAIL",
                ],
                "recency_score": 0.22,
                "recency_threshold": 0.30,
                "recency_distance": -0.08,
            },
            {
                "type": "BLEND_DECISION",
                "ts": "2026-06-18T01:00:06Z",
                "market_ticker": "KXCHAIN",
                "venue": "kalshi",
                "readiness_edge": 0.045,
            },
            {
                "type": "SKIPPED",
                "ts": "2026-06-18T01:00:07Z",
                "ticker": "KXCHAIN",
                "venue": "polymarket_us",
                "reason": "G6_recency_score",
            },
        ],
    )

    report = module.build_money_path_report(log_path, since="2026-06-18T00:00:00Z")

    assert report["summary"]["candidates"] == 1
    assert report["summary"]["terminal_counts"] == {"SKIPPED": 1}
    assert report["candidates"] == [
        {
            "opportunity_ts": "2026-06-18T01:00:00+00:00",
            "ticker": "KXCHAIN",
            "source": "AP News",
            "opportunity_edge": 0.12,
            "gate_ts": "2026-06-18T01:00:04+00:00",
            "gate_passed": False,
            "gate_failed": ["G6_recency_score"],
            "blend_ts": "2026-06-18T01:00:06+00:00",
            "blend_venue": "kalshi",
            "readiness_edge": 0.045,
            "terminal_ts": "2026-06-18T01:00:07+00:00",
            "terminal_type": "SKIPPED",
            "terminal_venue": "polymarket_us",
            "terminal_reason": "G6_recency_score",
            "recency_score": 0.22,
            "recency_threshold": 0.30,
            "recency_distance": -0.08,
            "measurement_gap": False,
        }
    ]


def test_build_report_reads_directory_filters_window_and_summarizes_blockers(tmp_path):
    module = _load_module()
    root = tmp_path / "logs"
    write_jsonl(
        root / "archive" / "old.jsonl",
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-17T23:59:59Z",
                "ticker": "KXOLD",
            }
        ],
    )
    write_jsonl(
        root / "live" / "trades.jsonl",
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-18T01:01:00Z",
                "ticker": "KXMISS",
                "reason": "no_keywords",
                "source": "Reuters",
            },
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-18T01:02:00Z",
                "ticker": "KXMISS",
                "source": "Reuters",
            },
            {
                "type": "GATE_SUMMARY",
                "ts": "2026-06-18T01:02:02Z",
                "ticker": "KXMISS",
                "failed_gate": "G6_recency_score",
            },
            {
                "type": "BLEND_DECISION",
                "ts": "2026-06-18T01:02:03Z",
                "ticker": "KXMISS",
                "venue": "kalshi",
            },
            {
                "type": "SKIPPED",
                "ts": "2026-06-18T01:02:04Z",
                "ticker": "KXMISS",
                "reason": "G6_recency_score",
            },
            {
                "type": "PAPER_TRADE",
                "ts": "2026-06-18T02:00:00Z",
                "ticker": "KXOUTSIDE",
            },
        ],
    )
    app_log = tmp_path / "bot.log"
    app_log.write_text(
        "2026-06-18 01:15:00,000 - WARNING - Kalshi WS Markets not found: KXMISS\n"
        "2026-06-18 02:10:00,000 - WARNING - Kalshi WS Markets not found: KXOUTSIDE\n",
        encoding="utf-8",
    )

    report = module.build_money_path_report(
        root,
        since="2026-06-18T01:00:00Z",
        until="2026-06-18T01:59:59Z",
        app_log_path=app_log,
    )

    assert [row["ticker"] for row in report["candidates"]] == ["KXMISS"]
    assert report["candidates"][0]["measurement_gap"] is True
    assert report["no_keywords"] == {
        "count": 1,
        "by_source": {"Reuters": 1},
        "tickers": {"KXMISS": 1},
    }
    assert report["app_warnings"]["markets_not_found"] == {
        "count": 1,
        "examples": [
            "2026-06-18 01:15:00,000 - WARNING - Kalshi WS Markets not found: KXMISS"
        ],
    }


def test_repeated_ticker_chains_consume_gate_blend_and_terminal_once(tmp_path):
    module = _load_module()
    log_path = tmp_path / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {"type": "OPPORTUNITY", "ts": "2026-06-18T01:00:00Z", "ticker": "KXRETRY"},
            {"type": "OPPORTUNITY", "ts": "2026-06-18T01:00:01Z", "ticker": "KXRETRY"},
            {
                "type": "GATE_SUMMARY",
                "ts": "2026-06-18T01:00:02Z",
                "ticker": "KXRETRY",
                "gate_chain": ["G6_recency_score: FAIL"],
            },
            {
                "type": "BLEND_DECISION",
                "ts": "2026-06-18T01:00:03Z",
                "ticker": "KXRETRY",
            },
            {
                "type": "SKIPPED",
                "ts": "2026-06-18T01:00:04Z",
                "ticker": "KXRETRY",
                "reason": "G6_recency_score",
            },
            {
                "type": "GATE_SUMMARY",
                "ts": "2026-06-18T01:00:05Z",
                "ticker": "KXRETRY",
                "gate_chain": ["G1: PASS"],
            },
            {
                "type": "BLEND_DECISION",
                "ts": "2026-06-18T01:00:06Z",
                "ticker": "KXRETRY",
            },
            {
                "type": "PAPER_TRADE",
                "ts": "2026-06-18T01:00:07Z",
                "ticker": "KXRETRY",
            },
        ],
    )

    report = module.build_money_path_report(log_path, since="2026-06-18T00:00:00Z")

    assert [row["terminal_type"] for row in report["candidates"]] == [
        "SKIPPED",
        "PAPER_TRADE",
    ]
    assert [row["terminal_ts"] for row in report["candidates"]] == [
        "2026-06-18T01:00:04+00:00",
        "2026-06-18T01:00:07+00:00",
    ]
    assert report["summary"]["terminal_counts"] == {"PAPER_TRADE": 1, "SKIPPED": 1}


def test_same_timestamp_chain_does_not_attach_prior_file_order_event(tmp_path):
    module = _load_module()
    log_path = tmp_path / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "GATE_SUMMARY",
                "ts": "2026-06-18T01:00:00Z",
                "ticker": "KXCOARSE",
                "gate_chain": ["G6_recency_score: FAIL"],
            },
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-18T01:00:00Z",
                "ticker": "KXCOARSE",
            },
            {
                "type": "GATE_SUMMARY",
                "ts": "2026-06-18T01:00:00Z",
                "ticker": "KXCOARSE",
                "gate_chain": ["G1: PASS"],
            },
            {
                "type": "BLEND_DECISION",
                "ts": "2026-06-18T01:00:00Z",
                "ticker": "KXCOARSE",
            },
            {
                "type": "PAPER_TRADE",
                "ts": "2026-06-18T01:00:00Z",
                "ticker": "KXCOARSE",
            },
        ],
    )

    report = module.build_money_path_report(log_path, since="2026-06-18T00:00:00Z")

    assert report["candidates"][0]["gate_failed"] == []
    assert report["candidates"][0]["terminal_type"] == "PAPER_TRADE"


def test_process_start_to_log_boot_gap_reports_resolution_only_losses(tmp_path):
    module = _load_module()
    log_path = tmp_path / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "PAPER_RESOLUTION",
                "ts": "2026-06-24T12:00:00Z",
                "ticker": "PM-LOSS-1",
                "venue": "polymarket_us",
                "pnl_dollars": -4.15,
            },
            {
                "type": "PAPER_RESOLUTION",
                "ts": "2026-06-25T12:00:00Z",
                "ticker": "KXLOSS2",
                "venue": "kalshi",
                "pnl_dollars": -1.30,
            },
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-26T01:00:00Z",
                "ticker": "KXPOSTBOOT",
            },
        ],
    )

    report = module.build_money_path_report(
        log_path,
        since="2026-06-24T11:20:22Z",
        process_start="2026-06-24T11:20:22Z",
        log_boot="2026-06-26T00:00:41Z",
    )

    assert report["summary"]["candidates"] == 1
    assert report["legacy_resolutions_between_process_start_and_log_boot"] == {
        "count": 2,
        "pnl_total": pytest.approx(-5.45),
        "tickers": ["PM-LOSS-1", "KXLOSS2"],
    }


def test_cli_text_output_includes_candidate_and_blocker_summary(tmp_path, capsys):
    module = _load_module()
    log_path = tmp_path / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-18T01:00:00Z",
                "ticker": "KXCLI",
            },
            {
                "type": "SKIPPED",
                "ts": "2026-06-18T01:00:03Z",
                "ticker": "KXCLI",
                "reason": "G6_recency_score",
            },
        ],
    )

    exit_code = module.main([str(log_path), "--since", "2026-06-18T00:00:00Z"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Candidates: 1" in out
    assert "Terminal: SKIPPED=1" in out
    assert "KXCLI" in out
    assert "measurement_gap=true" in out


def test_polymarket_settlement_feedback_proof_marks_small_resolved_sample(tmp_path):
    module = _load_module()
    log_path = tmp_path / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-06-20T01:00:00Z",
                "ticker": "PM-IRAN-2026-06-20",
                "source": "AP News",
            },
            {
                "type": "BLEND_DECISION",
                "ts": "2026-06-20T01:00:01Z",
                "ticker": "PM-IRAN-2026-06-20",
                "venue": "polymarket_us",
            },
            {
                "type": "PAPER_TRADE",
                "ts": "2026-06-20T01:00:02Z",
                "ticker": "PM-IRAN-2026-06-20",
                "venue": "polymarket_us",
                "trade_id": "trade-pm-1",
                "side": "YES",
                "executed_edge": 0.08,
            },
            {
                "type": "PAPER_RESOLUTION",
                "ts": "2026-06-20T02:00:00Z",
                "ticker": "PM-IRAN-2026-06-20",
                "venue": "polymarket_us",
                "trade_id": "trade-pm-1",
                "resolved": True,
                "pnl_dollars": 1.7,
                "outcome": "win",
            },
            {
                "type": "MATCH_LLM_REVIEW",
                "ts": "2026-06-20T02:05:00Z",
                "ticker": "PM-IRAN-2026-06-20",
                "market_prefix": "polymarket_us:iran",
                "venue": "polymarket_us",
                "keywords": ["iran"],
            },
        ],
    )

    report = module.build_money_path_report(log_path, since="2026-06-20T00:00:00Z")

    proof = report["polymarket_settlement_feedback"]
    assert proof["status"] == "insufficient_sample"
    assert proof["resolved_count"] == 1
    assert proof["min_resolved_required"] == 10
    assert proof["proof_rows"] == [
        {
            "ticker": "PM-IRAN-2026-06-20",
            "trade_id": "trade-pm-1",
            "paper_trade_ts": "2026-06-20T01:00:02+00:00",
            "resolution_ts": "2026-06-20T02:00:00+00:00",
            "pnl_dollars": 1.7,
            "outcome": "win",
            "feedback_ts": "2026-06-20T02:05:00+00:00",
            "market_prefix": "polymarket_us:iran",
        }
    ]

    rendered = module.format_text_report(report)
    assert "Polymarket settlement feedback: insufficient_sample (1/10 resolved)" in rendered
    assert (
        "PM-IRAN-2026-06-20 trade_id=trade-pm-1 pnl=1.7 "
        "feedback=2026-06-20T02:05:00+00:00 prefix=polymarket_us:iran"
    ) in rendered
