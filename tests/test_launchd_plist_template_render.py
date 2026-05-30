from __future__ import annotations

import plistlib
import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "ops/launchd/install.sh"

EXPECTED_LABELS = {
    "com.jake.kalshi-bot",
    "com.jake.kalshi-bothealth",
    "com.jake.kalshi-daily-review",
    "com.jake.kalshi-match-feedback-aggregator",
    "com.jake.kalshi-soak-check",
    "com.kalshi.db-backup",
    "com.kalshi.governance.fast",
    "com.kalshi.governance.deep",
}


def _rendered_plists() -> dict[str, dict]:
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--print"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    rendered: dict[str, dict] = {}
    for match in re.finditer(r"<\?xml.*?</plist>", result.stdout, flags=re.DOTALL):
        xml = match.group(0).encode()
        plist = plistlib.loads(xml)
        rendered[plist["Label"]] = plist
    return rendered


def test_all_launchd_templates_render_valid_plists():
    rendered = _rendered_plists()

    assert set(rendered) == EXPECTED_LABELS
    for label, plist in rendered.items():
        assert plist["WorkingDirectory"] == str(REPO_ROOT), label
        assert "@REPO_ROOT@" not in str(plist), label
        assert "@VENV_PYTHON@" not in str(plist), label
        assert "@GOVERNANCE_LLM_MODEL@" not in str(plist), label

    for label in ("com.kalshi.governance.fast", "com.kalshi.governance.deep"):
        args = rendered[label]["ProgramArguments"]
        assert "--run-source" in args, label
        assert args[args.index("--run-source") + 1] == "launchd", label


def test_launchd_templates_match_installed_plists_when_present():
    installed_dir = Path.home() / "Library/LaunchAgents"
    missing = [
        label
        for label in EXPECTED_LABELS
        if not (installed_dir / f"{label}.plist").exists()
    ]
    if missing:
        import pytest

        pytest.skip(f"installed launchd plists absent: {', '.join(sorted(missing))}")

    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/python"),
            str(REPO_ROOT / "scripts/launchd_template_equivalence_audit.py"),
            "--installed",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_launchd_templates_match_canonical_fixtures_when_present():
    fixtures_dir = REPO_ROOT / "tests/fixtures/installed_plists"
    missing = [
        label
        for label in EXPECTED_LABELS
        if not (fixtures_dir / f"{label}.plist").exists()
    ]
    if missing:
        import pytest

        pytest.skip(f"canonical installed-plist fixtures absent: {', '.join(sorted(missing))}")

    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/python"),
            str(REPO_ROOT / "scripts/launchd_template_equivalence_audit.py"),
            "--fixtures",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
