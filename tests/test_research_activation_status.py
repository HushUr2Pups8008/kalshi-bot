from __future__ import annotations

from pathlib import Path

from scripts.research_activation_status import evaluate_activation_profile


def test_shadow_profile_does_not_require_operator_only_brave_probe_configuration(
    tmp_path,
):
    repo_root = Path(__file__).resolve().parents[1]
    profile = repo_root / "docs" / "governance" / "research-shadow.env.example"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "REAL_WEB_RESEARCH_MODE=shadow",
                "REAL_WEB_RESEARCH_MAX_QUERIES=6",
                "REAL_WEB_RESEARCH_TIMEOUT_SECONDS=12.0",
                "ENABLE_RESEARCH_PREWARM_TASK=true",
                "RESEARCH_PREWARM_INTERVAL_SECONDS=300",
                "RESEARCH_PREWARM_MAX_MARKETS=25",
                "RESEARCH_PREWARM_MAX_PAGES=5",
                "RESEARCH_PREWARM_TARGET_COOLDOWN_SECONDS=1800",
                "LIVE_TRADING_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )

    assessment = evaluate_activation_profile(repo_root, profile, env_path=env_path)

    assert assessment.ok
    assert "ENABLE_BRAVE_SEARCH_SHADOW" not in assessment.profile_values
    assert "BRAVE_SEARCH_API_KEY" not in assessment.profile_values


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


def test_activation_status_allows_missing_defaulted_sourceable_fallback(tmp_path):
    profile = tmp_path / "profile.env"
    env_path = tmp_path / ".env"
    profile.write_text(
        "\n".join(
            [
                "REAL_WEB_RESEARCH_MODE=shadow",
                "ENABLE_RESEARCH_PREWARM_TASK=true",
                "RESEARCH_PREWARM_SOURCEABLE_SERIES_FALLBACK=KXGDP,KXCPI,KXFED,KXNASDAQ100,KXBTC,KXETH,KXHIGHNY,KXMLB,KXNBA",
                "LIVE_TRADING_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )
    env_path.write_text(
        "\n".join(
            [
                "REAL_WEB_RESEARCH_MODE=shadow",
                "ENABLE_RESEARCH_PREWARM_TASK=true",
                "LIVE_TRADING_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )

    assessment = evaluate_activation_profile(Path.cwd(), profile, env_path=env_path)

    assert assessment.ok
    assert "RESEARCH_PREWARM_SOURCEABLE_SERIES_FALLBACK" not in assessment.missing


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
