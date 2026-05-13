"""CI wrapper for bothealth shell verdict regression tests."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_bothealth_verdict_shell_regression_suite():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "tests" / "shell" / "test_bothealth_verdict.sh"

    result = subprocess.run(
        ["bash", str(script)],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
