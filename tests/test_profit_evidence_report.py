import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.profit_evidence_report import (
    build_profit_evidence_report,
    collect_replay_evidence,
    readiness_verdict,
    summarize_paper_expectancy,
)
import scripts.profit_evidence_report as profit_evidence_report


def _create_paper_db(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE paper_trades (
            trade_id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            contracts INTEGER NOT NULL,
            price_cents INTEGER NOT NULL,
            cost_dollars REAL NOT NULL,
            estimated_prob REAL NOT NULL,
            edge REAL NOT NULL,
            resolved INTEGER DEFAULT 0,
            pnl_dollars REAL,
            notional_bankroll_before REAL,
            notional_bankroll_after REAL,
            venue TEXT DEFAULT 'kalshi'
        )
        """
    )
    for row in rows:
        conn.execute(
            """
            INSERT INTO paper_trades (
                trade_id, ts, ticker, side, contracts, price_cents, cost_dollars,
                estimated_prob, edge, resolved, pnl_dollars,
                notional_bankroll_before, notional_bankroll_after, venue
            )
            VALUES (
                :trade_id, :ts, :ticker, :side, :contracts, :price_cents,
                :cost_dollars, :estimated_prob, :edge, :resolved, :pnl_dollars,
                :notional_bankroll_before, :notional_bankroll_after, :venue
            )
            """,
            row,
        )
    conn.commit()
    conn.close()


def _paper_row(
    trade_id: str,
    *,
    resolved: bool,
    pnl: float | None,
    edge: float,
    before: float = 100.0,
    after: float | None = None,
    venue: str = "kalshi",
) -> dict:
    return {
        "trade_id": trade_id,
        "ts": "2026-06-20T12:00:00Z",
        "ticker": f"KXTEST-{trade_id}",
        "side": "yes",
        "contracts": 1,
        "price_cents": 50,
        "cost_dollars": 0.50,
        "estimated_prob": 0.57,
        "edge": edge,
        "resolved": 1 if resolved else 0,
        "pnl_dollars": pnl,
        "notional_bankroll_before": before,
        "notional_bankroll_after": before + (pnl or 0.0) if after is None else after,
        "venue": venue,
    }


@pytest.fixture(autouse=True)
def _canonical_delivery_for_synthetic_paper_rows(monkeypatch):
    """Keep arithmetic fixtures explicit about their synthetic delivery state."""

    def delivery_complete_ids(paper_db: Path) -> set[str]:
        with sqlite3.connect(paper_db) as conn:
            return {str(row[0]) for row in conn.execute("SELECT trade_id FROM paper_trades")}

    monkeypatch.setattr(
        profit_evidence_report,
        "_canonical_delivery_complete_trade_ids",
        delivery_complete_ids,
        raising=False,
    )


def test_paper_expectancy_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "paper_trades.db"
    _create_paper_db(
        db_path,
        [
            _paper_row("win-kalshi", resolved=True, pnl=1.20, edge=0.08, venue="kalshi"),
            _paper_row("loss-kalshi", resolved=True, pnl=-0.30, edge=0.02, venue="kalshi"),
            _paper_row("win-poly", resolved=True, pnl=0.60, edge=0.11, venue="polymarket"),
            _paper_row("open-poly", resolved=False, pnl=None, edge=0.04, venue="polymarket"),
        ],
    )

    summary = summarize_paper_expectancy(db_path)

    assert summary.total_trades == 4
    assert summary.resolved_trades == 3
    assert summary.open_trades == 1
    assert summary.wins == 2
    assert summary.losses == 1
    assert summary.win_rate == pytest.approx(2 / 3)
    assert summary.net_pnl == pytest.approx(1.50)
    assert summary.expectancy_per_resolved_trade == pytest.approx(0.50)
    assert summary.avg_edge == pytest.approx(0.0625)
    assert summary.edge_buckets["0.00_to_0.05"]["resolved_trades"] == 1
    assert summary.edge_buckets["0.00_to_0.05"]["net_pnl"] == pytest.approx(-0.30)
    assert summary.edge_buckets["0.05_to_0.10"]["net_pnl"] == pytest.approx(1.20)
    assert summary.edge_buckets["gte_0.10"]["net_pnl"] == pytest.approx(0.60)
    assert summary.by_venue["kalshi"].resolved_trades == 2
    assert summary.by_venue["polymarket"].open_trades == 1


def test_replay_artifacts_distinguish_scored_from_insufficient_corpus(tmp_path: Path) -> None:
    replay_root = tmp_path / "edge_replay"
    scored_dir = replay_root / "cycle"
    scored_dir.mkdir(parents=True)
    (scored_dir / "counterfactual_scores.json").write_text(
        json.dumps(
            {
                "summary": {
                    "overall": {
                        "trades": 24,
                        "win_rate": 0.58,
                        "pnl": 4.8,
                        "avg_pnl_per_trade": 0.20,
                        "ev_ci_95_lo": 0.04,
                        "ev_ci_95_hi": 0.36,
                    }
                }
            }
        )
    )
    head_dir = replay_root / "ci_runs" / "HEAD"
    head_dir.mkdir(parents=True)
    (head_dir / "verdict.json").write_text(
        json.dumps({"pass": False, "failure_reason": "insufficient corpus"})
    )
    (head_dir / "rule4_table.json").write_text("null")

    evidence = collect_replay_evidence(replay_root)

    scored = [item for item in evidence if item.status == "scored"]
    insufficient = [item for item in evidence if item.status == "insufficient_corpus"]
    assert len(scored) == 1
    assert scored[0].trade_count == 24
    assert scored[0].win_rate == pytest.approx(0.58)
    assert scored[0].realized_pnl == pytest.approx(4.8)
    assert scored[0].per_trade_ev == pytest.approx(0.20)
    assert scored[0].ev_ci_95_lo == pytest.approx(0.04)
    assert len(insufficient) == 2
    assert all(item.trade_count is None for item in insufficient)


def test_verdict_uses_current_head_replay_over_stale_scored_cycle(tmp_path: Path) -> None:
    db_path = tmp_path / "paper_trades.db"
    _create_paper_db(
        db_path,
        [
            _paper_row(f"trade-{idx}", resolved=True, pnl=0.30, edge=0.07)
            for idx in range(20)
        ],
    )
    replay_root = tmp_path / "edge_replay"
    stale_dir = replay_root / "cycle99"
    stale_dir.mkdir(parents=True)
    (stale_dir / "counterfactual_scores.json").write_text(
        json.dumps(
            {
                "trade_count": 25,
                "win_rate": 0.60,
                "realized_pnl": 5.0,
                "per_trade_ev": 0.20,
                "ev_ci_95_lo": 0.05,
                "ev_ci_95_hi": 0.35,
            }
        )
    )
    head_dir = replay_root / "ci_runs" / "HEAD"
    head_dir.mkdir(parents=True)
    (head_dir / "verdict.json").write_text(
        json.dumps({"pass": False, "failure_reason": "insufficient corpus"})
    )

    report = build_profit_evidence_report(db_path, replay_root)

    assert report.verdict.ready is False
    assert "missing current replay evidence" in report.verdict.reasons


def test_replay_provenance_marks_head_t0_and_historical_cycle_non_current(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "paper_trades.db"
    _create_paper_db(
        db_path,
        [_paper_row(f"trade-{idx}", resolved=True, pnl=0.30, edge=0.07) for idx in range(20)],
    )
    replay_root = tmp_path / "edge_replay"
    cycle_dir = replay_root / "cycle99"
    cycle_dir.mkdir(parents=True)
    (cycle_dir / "counterfactual_scores.json").write_text(
        json.dumps(
            {
                "trade_count": 25,
                "per_trade_ev": 0.20,
                "ev_ci_95_lo": 0.05,
            }
        )
    )
    head_dir = replay_root / "ci_runs" / "HEAD"
    head_dir.mkdir(parents=True)
    (head_dir / "verdict.json").write_text(
        json.dumps(
            {
                "tier": "T0",
                "pass": True,
                "rule4": None,
                "notes": "T0: Rule 2 exempt - passing without corpus/scenarios/cache.",
            }
        )
    )
    (head_dir / "rule4_table.json").write_text("null")

    report = build_profit_evidence_report(db_path, replay_root)
    provenance_by_source = {item.source: item.provenance for item in report.replay}

    assert provenance_by_source["cycle99/counterfactual_scores.json"] == "historical_cycle"
    assert provenance_by_source["ci_runs/HEAD/verdict.json"] == "head_t0_no_corpus"
    assert provenance_by_source["ci_runs/HEAD/rule4_table.json"] == "head_unscored"
    assert "missing current replay evidence" in report.verdict.reasons


def test_replay_provenance_excludes_head_explicit_subset_score_from_current_proof(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "paper_trades.db"
    _create_paper_db(
        db_path,
        [_paper_row(f"trade-{idx}", resolved=True, pnl=0.30, edge=0.07) for idx in range(20)],
    )
    replay_root = tmp_path / "edge_replay"
    head_dir = replay_root / "ci_runs" / "HEAD"
    head_dir.mkdir(parents=True)
    (head_dir / "verdict.json").write_text(
        json.dumps(
            {
                "tier": "T1",
                "pass": True,
                "notes": "WARNING (safeguard D): explicit corpora subset supplied",
                "rule4": {
                    "trade_count": 25,
                    "per_trade_ev": 0.20,
                    "ev_ci_95_lo": 0.05,
                },
            }
        )
    )

    report = build_profit_evidence_report(db_path, replay_root)
    head = next(item for item in report.replay if item.source.endswith("verdict.json"))

    assert head.status == "scored"
    assert head.provenance == "head_scored_explicit_subset"
    assert "missing current replay evidence" in report.verdict.reasons


def test_scored_failed_head_replay_is_current_negative_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "paper_trades.db"
    _create_paper_db(
        db_path,
        [_paper_row(f"trade-{idx}", resolved=True, pnl=0.30, edge=0.07) for idx in range(20)],
    )
    replay_root = tmp_path / "edge_replay"
    head_dir = replay_root / "ci_runs" / "HEAD"
    head_dir.mkdir(parents=True)
    (head_dir / "verdict.json").write_text(
        json.dumps(
            {
                "tier": "T1",
                "pass": False,
                "rule4": {
                    "trade_count": 25,
                    "per_trade_ev": -0.20,
                    "ev_ci_95_lo": -0.05,
                },
            }
        )
    )

    report = build_profit_evidence_report(db_path, replay_root)
    head = next(item for item in report.replay if item.source.endswith("verdict.json"))

    assert head.provenance == "head_scored_failed"
    assert "current replay gate failed" in report.verdict.reasons
    assert "missing current replay evidence" not in report.verdict.reasons


def test_verdict_requires_paper_and_replay_proof(tmp_path: Path) -> None:
    db_path = tmp_path / "paper_trades.db"
    rows = [
        _paper_row(
            f"trade-{idx}",
            resolved=True,
            pnl=0.30 if idx < 12 else -0.10,
            edge=0.07,
            before=100.0,
            after=100.0 - 5.0 + idx * 0.2,
        )
        for idx in range(20)
    ]
    _create_paper_db(db_path, rows)
    replay_root = tmp_path / "edge_replay"
    replay_root.mkdir()
    (replay_root / "counterfactual_scores.json").write_text(
        json.dumps(
            {
                "trade_count": 25,
                "win_rate": 0.56,
                "realized_pnl": 5.0,
                "per_trade_ev": 0.20,
                "ev_ci_95_lo": 0.03,
                "ev_ci_95_hi": 0.37,
            }
        )
    )
    report = build_profit_evidence_report(db_path, replay_root)

    verdict = readiness_verdict(report.paper, report.replay)

    assert verdict.ready is False
    assert verdict.label == "not live-ready"
    assert "independent realized-profit evidence is unavailable" in verdict.reasons

    verdict = readiness_verdict(
        report.paper,
        report.replay,
        min_resolved_trades=21,
    )
    assert verdict.ready is False
    assert "resolved sample 20 below 21" in verdict.reasons

    bad_replay = [item for item in report.replay if item.status == "scored"][0].with_updates(
        provenance="head_scored_attested",
        per_trade_ev=-0.01,
        ev_ci_95_lo=-0.05,
    )
    verdict = readiness_verdict(report.paper, [bad_replay])
    assert verdict.ready is False
    assert "replay EV evidence failed" in verdict.reasons

    drawdown_db = tmp_path / "drawdown.db"
    _create_paper_db(
        drawdown_db,
        [
            _paper_row(
                f"drawdown-{idx}",
                resolved=True,
                pnl=0.20,
                edge=0.07,
                before=100.0,
                after=70.0,
            )
            for idx in range(20)
        ],
    )
    drawdown_report = build_profit_evidence_report(drawdown_db, replay_root)
    verdict = readiness_verdict(drawdown_report.paper, drawdown_report.replay)
    assert verdict.ready is False
    assert "drawdown 30.0% above 20.0%" in verdict.reasons


def test_canonical_paper_delivery_is_not_realized_profit_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "paper_trades.db"
    _create_paper_db(
        db_path,
        [
            _paper_row(f"trade-{idx}", resolved=True, pnl=0.30, edge=0.07)
            for idx in range(20)
        ],
    )
    replay_root = tmp_path / "edge_replay"
    replay_root.mkdir()
    (replay_root / "counterfactual_scores.json").write_text(
        json.dumps(
            {
                "trade_count": 25,
                "win_rate": 0.60,
                "realized_pnl": 5.0,
                "per_trade_ev": 0.20,
                "ev_ci_95_lo": 0.05,
                "ev_ci_95_hi": 0.35,
            }
        )
    )

    report = build_profit_evidence_report(db_path, replay_root)

    assert report.paper.resolved_trades == 20
    assert report.paper.canonical_delivery_complete_resolved_trades == 20
    assert report.paper.profit_attested_resolved_trades == 0
    assert report.verdict.ready is False
    assert "independent realized-profit evidence is unavailable" in report.verdict.reasons


def test_cli_emits_json_and_text_sections(tmp_path: Path) -> None:
    db_path = tmp_path / "paper_trades.db"
    _create_paper_db(
        db_path,
        [
            _paper_row("win", resolved=True, pnl=0.25, edge=0.08),
            _paper_row("open", resolved=False, pnl=None, edge=0.06),
        ],
    )
    replay_root = tmp_path / "edge_replay"
    replay_root.mkdir()

    script_path = Path(__file__).parents[1] / "scripts" / "profit_evidence_report.py"
    json_run = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--paper-db",
            str(db_path),
            "--edge-replay-root",
            str(replay_root),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(json_run.stdout)

    assert payload["paper_expectancy"]["total_trades"] == 2
    assert payload["readiness_verdict"]["ready"] is False
    assert payload["readiness_verdict"]["label"] == "not live-ready"
    assert "missing replay evidence" in payload["readiness_verdict"]["reasons"]

    text_run = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--paper-db",
            str(db_path),
            "--edge-replay-root",
            str(replay_root),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "PAPER EXPECTANCY" in text_run.stdout
    assert "REPLAY EVIDENCE" in text_run.stdout
    assert "READINESS VERDICT" in text_run.stdout
    assert "not live-ready" in text_run.stdout
