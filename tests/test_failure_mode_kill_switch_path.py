from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = REPO_ROOT / "docs/governance/2026-05-05-kill-switch-fire-procedure-runbook.md"


def test_kill_switch_runbook_pins_detection_halt_revert_and_restart_commands():
    body = RUNBOOK.read_text(encoding="utf-8")

    assert "logs/governance/decisions.jsonl" in body
    assert "KILL_SWITCH count" in body
    assert "launchctl bootout gui/$(id -u)" in body
    assert "git revert <suspect-commit-sha>" in body
    assert "git push origin main" in body
    assert "launchctl bootstrap gui/$(id -u)" in body
    assert "docs/profit_path_debt_log.md" in body
    assert "kill-switch-fire-$(date -u +%Y-%m-%d)" in body
