from pathlib import Path

from scripts import migrate_runtime_outputs


def test_plan_moves_generated_reports_without_raw_trades(tmp_path: Path):
    repo = tmp_path
    (repo / "logs" / "reports").mkdir(parents=True)
    (repo / "logs" / "app").mkdir(parents=True)
    (repo / "logs" / "trades" / "live").mkdir(parents=True)
    (repo / "logs" / "reports" / "daily_review_20260530.txt").write_text("daily")
    (repo / "logs" / "app" / "bothealth_2026-05-30.md").write_text("health")
    (repo / "logs" / "trades" / "live" / "trades.jsonl").write_text("{}\n")

    moves = migrate_runtime_outputs.build_plan(repo=repo, output_root=repo / "logs")
    destinations = {move.destination.relative_to(repo).as_posix() for move in moves}
    sources = {move.source.relative_to(repo).as_posix() for move in moves}

    assert "logs/reports/daily/daily_review_20260530.txt" in destinations
    assert "logs/reports/health/bothealth_2026-05-30.md" in destinations
    assert "logs/trades/live/trades.jsonl" not in sources


def test_dry_run_does_not_move_files(tmp_path: Path):
    repo = tmp_path
    source = repo / "logs" / "reports" / "report_20260530.txt"
    source.parent.mkdir(parents=True)
    source.write_text("performance")

    moves = migrate_runtime_outputs.build_plan(repo=repo, output_root=repo / "logs")
    migrate_runtime_outputs.execute_plan(moves, apply=False)

    assert source.exists()
    assert not (repo / "logs" / "reports" / "performance" / source.name).exists()
