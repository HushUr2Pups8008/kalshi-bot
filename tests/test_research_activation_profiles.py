from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _parse_env_example(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_shadow_research_activation_profile_collects_evidence_without_promotion():
    profile = REPO_ROOT / "docs" / "governance" / "research-shadow.env.example"

    values = _parse_env_example(profile)

    assert values["REAL_WEB_RESEARCH_MODE"] == "shadow"
    assert values["ENABLE_RESEARCH_PREWARM_TASK"] == "true"
    assert int(values["REAL_WEB_RESEARCH_MAX_QUERIES"]) >= 6
    assert float(values["REAL_WEB_RESEARCH_TIMEOUT_SECONDS"]) >= 12.0
    assert int(values["RESEARCH_PREWARM_MAX_MARKETS"]) >= 25
    assert int(values["RESEARCH_PREWARM_MAX_PAGES"]) >= 5
    assert values.get("LIVE_TRADING_ENABLED") != "true"


def test_production_research_activation_profile_is_paper_safe_and_prewarmed():
    profile = REPO_ROOT / "docs" / "governance" / "research-production-paper.env.example"

    values = _parse_env_example(profile)

    assert values["REAL_WEB_RESEARCH_MODE"] == "production"
    assert values["ENABLE_RESEARCH_PREWARM_TASK"] == "true"
    assert int(values["REAL_WEB_RESEARCH_MAX_QUERIES"]) >= 6
    assert float(values["REAL_WEB_RESEARCH_TIMEOUT_SECONDS"]) >= 12.0
    assert int(values["RESEARCH_PREWARM_INTERVAL_SECONDS"]) <= 900
    assert int(values["RESEARCH_PREWARM_MAX_MARKETS"]) >= 25
    assert int(values["RESEARCH_PREWARM_MAX_PAGES"]) >= 5
    assert values["LIVE_TRADING_ENABLED"] == "false"


def test_env_example_points_operator_to_research_activation_profiles():
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "docs/governance/research-shadow.env.example" in env_example
    assert "docs/governance/research-production-paper.env.example" in env_example
