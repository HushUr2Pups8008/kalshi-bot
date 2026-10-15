"""A politics crash loop backs off and stops after five starts in an hour."""

from __future__ import annotations

import json

import scripts.politics_restart_guard as guard
import utils.event_news_session_halt as halt


def test_first_start_sleeps_zero_and_later_starts_back_off() -> None:
    now = 1_000_000.0
    stamps: list[float] = []
    sleeps: list[int] = []
    for offset in range(5):
        action, sleep_seconds, stamps = guard.plan_restart(stamps, now + offset, cap=5)
        assert action == "start"
        sleeps.append(sleep_seconds)
    assert sleeps == [0, 30, 60, 90, 120]
    action, sleep_seconds, kept = guard.plan_restart(stamps, now + 5, cap=5)
    assert action == "capped"
    assert sleep_seconds == 0
    assert kept == stamps


def test_stamps_older_than_an_hour_do_not_count() -> None:
    now = 1_000_000.0
    action, sleep_seconds, stamps = guard.plan_restart([now - 3600], now, cap=5)
    assert action == "start"
    assert sleep_seconds == 0
    assert stamps == [now]


def test_cli_caps_at_five_and_writes_the_alert(monkeypatch, tmp_path, capsys) -> None:
    state = tmp_path / "restarts.json"
    alert = tmp_path / "alert.json"
    monkeypatch.setenv("POLITICS_RESTART_STATE", str(state))
    monkeypatch.setenv("POLITICS_RESTART_CAP", "5")
    monkeypatch.setattr(halt, "OPERATOR_ALERT_PATH", alert)
    monkeypatch.setattr(guard.time, "time", lambda: 1_000_000.0)

    for _ in range(5):
        assert guard.main() == 0
        assert capsys.readouterr().out.strip().isdigit()

    assert guard.main() == guard.CAP_EXIT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cap" in captured.err
    payload = json.loads(alert.read_text(encoding="utf-8"))
    assert payload["reason"] == "restart_cap"
    stored = json.loads(state.read_text(encoding="utf-8"))
    assert len(stored["stamps"]) == 5
