from __future__ import annotations

from pathlib import Path

from scripts.research_activation_status import evaluate_activation_profile


def test_activation_status_passes_when_env_matches_profile(tmp_path):
    profile = tmp_path / "profile.env"
    env_path = tmp_path / ".env"
    profile.write_text(
        "\n".join(
            [
                "REAL_WEB_RESEARCH_MODE=shadow",
                "ENABLE_RESEARCH_PREWARM_TASK=true",
                "LIVE_TRADING_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )
    env_path.write_text(profile.read_text(encoding="utf-8"), encoding="utf-8")

    assessment = evaluate_activation_profile(Path.cwd(), profile, env_path=env_path)

    assert assessment.ok
    assert assessment.missing == []
    assert assessment.mismatched == []
    assert assessment.unsafe == []


def test_activation_status_fails_when_research_settings_missing(tmp_path):
    profile = tmp_path / "profile.env"
    env_path = tmp_path / ".env"
    profile.write_text(
        "\n".join(
            [
                "REAL_WEB_RESEARCH_MODE=shadow",
                "ENABLE_RESEARCH_PREWARM_TASK=true",
                "LIVE_TRADING_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )
    env_path.write_text("LIVE_TRADING_ENABLED=false\n", encoding="utf-8")

    assessment = evaluate_activation_profile(Path.cwd(), profile, env_path=env_path)

    assert not assessment.ok
    assert assessment.missing == [
        "REAL_WEB_RESEARCH_MODE",
        "ENABLE_RESEARCH_PREWARM_TASK",
    ]


def test_activation_status_fails_when_env_differs_from_profile(tmp_path):
    profile = tmp_path / "profile.env"
    env_path = tmp_path / ".env"
    profile.write_text(
        "\n".join(
            [
                "REAL_WEB_RESEARCH_MODE=production",
                "ENABLE_RESEARCH_PREWARM_TASK=true",
                "LIVE_TRADING_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )
    env_path.write_text(
        "\n".join(
            [
                "REAL_WEB_RESEARCH_MODE=off",
                "ENABLE_RESEARCH_PREWARM_TASK=false",
                "LIVE_TRADING_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )

    assessment = evaluate_activation_profile(Path.cwd(), profile, env_path=env_path)

    assert not assessment.ok
    assert ("REAL_WEB_RESEARCH_MODE", "production", "off") in assessment.mismatched
    assert ("ENABLE_RESEARCH_PREWARM_TASK", "true", "false") in assessment.mismatched


def test_activation_status_fails_closed_when_live_trading_enabled(tmp_path):
    profile = tmp_path / "profile.env"
    env_path = tmp_path / ".env"
    profile.write_text(
        "\n".join(
            [
                "REAL_WEB_RESEARCH_MODE=production",
                "ENABLE_RESEARCH_PREWARM_TASK=true",
                "LIVE_TRADING_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )
    env_path.write_text(
        "\n".join(
            [
                "REAL_WEB_RESEARCH_MODE=production",
                "ENABLE_RESEARCH_PREWARM_TASK=true",
                "LIVE_TRADING_ENABLED=true",
            ]
        ),
        encoding="utf-8",
    )

    assessment = evaluate_activation_profile(Path.cwd(), profile, env_path=env_path)

    assert not assessment.ok
    assert "LIVE_TRADING_ENABLED=true is not allowed for research activation profiles" in (
        assessment.unsafe
    )
