from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
KILL_SWITCH_RUNBOOK = REPO_ROOT / "docs/governance/2026-05-05-kill-switch-fire-procedure-runbook.md"
DEAD_BOT_RUNBOOK = REPO_ROOT / "docs/governance/2026-05-05-mac-studio-dead-bot-reboot-procedure.md"


@pytest.mark.xfail(reason="Claude T8 KILL_SWITCH fire procedure runbook not landed yet", strict=True)
def test_kill_switch_runbook_has_required_command_shapes():
    body = KILL_SWITCH_RUNBOOK.read_text(encoding="utf-8")

    assert "launchctl" in body
    assert "GOVERNANCE_KILL_SWITCH" in body or "KILL_SWITCH" in body
    assert "scripts/bothealth.sh" in body
    assert "scripts/operator_alert_routing_audit.sh" in body
    assert "docs/profit_path_debt_log.md" in body


@pytest.mark.xfail(reason="Claude T9 Mac Studio dead-bot reboot runbook not landed yet", strict=True)
def test_mac_studio_dead_bot_runbook_has_required_command_shapes():
    body = DEAD_BOT_RUNBOOK.read_text(encoding="utf-8")

    assert "launchctl" in body
    assert "zsh -ic \"botcheck\"" in body or "botcheck" in body
    assert "scripts/db_backup_health_audit.sh" in body
    assert "logs/app/bot.log" in body
    assert "scripts/bothealth.sh" in body
