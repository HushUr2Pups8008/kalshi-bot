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
