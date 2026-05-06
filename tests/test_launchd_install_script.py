"""Tests for ops/launchd/install.sh — plist templating + per-machine substitution.

The install script is the load-bearing portability mechanism between the
MacBook (kalshi_bot) and Mac Studio (kalshi-bot) hosts. Regressions here
would silently break governance-agent bootstrap on either host, so the
substitution behavior is pinned via these tests.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "ops" / "launchd" / "install.sh"
TEMPLATES = (
    REPO_ROOT / "ops" / "launchd" / "com.jake.kalshi-bot.plist.template",
    REPO_ROOT / "ops" / "launchd" / "com.jake.kalshi-bothealth.plist.template",
    REPO_ROOT / "ops" / "launchd" / "com.jake.kalshi-soak-check.plist.template",
    REPO_ROOT / "ops" / "launchd" / "com.kalshi.db-backup.plist.template",
    REPO_ROOT / "ops" / "launchd" / "com.kalshi.governance.fast.plist.template",
    REPO_ROOT / "ops" / "launchd" / "com.kalshi.governance.deep.plist.template",
)


def _run_print(env_overrides: dict | None = None) -> str:
    """Run `install.sh --print` with optional env overrides; return stdout."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--print"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        check=True,
    )
    return result.stdout


def test_install_script_exists_and_is_executable():
    assert INSTALL_SH.exists(), f"install.sh missing at {INSTALL_SH}"
    assert os.access(INSTALL_SH, os.X_OK), "install.sh is not executable"


def test_templates_exist():
    for tpl in TEMPLATES:
        assert tpl.exists(), f"template missing: {tpl}"


def test_templates_carry_unsubstituted_placeholders():
    """Source-of-truth invariant: templates must NOT have hardcoded paths.

    If this fails it usually means someone edited the template thinking they
    were editing a generated plist, which would defeat the portability story.
    """
    for tpl in TEMPLATES:
        body = tpl.read_text()
        assert "@REPO_ROOT@" in body, f"{tpl.name} missing placeholder @REPO_ROOT@"
        if "db-backup" not in tpl.name and "bothealth" not in tpl.name and "soak-check" not in tpl.name:
            assert "@VENV_PYTHON@" in body, f"{tpl.name} missing placeholder @VENV_PYTHON@"
        if "governance" in tpl.name:
            assert "@GOVERNANCE_LLM_MODEL@" in body, (
                f"{tpl.name} missing placeholder @GOVERNANCE_LLM_MODEL@"
            )
        # Hardcoded user paths must not exist in templates.
        assert "/Users/Jake" not in body, f"{tpl.name} contains stale MacBook path"
        assert "/Users/jacobparenti" not in body, (
            f"{tpl.name} contains a hardcoded current-user path; should use placeholder"
        )


def test_print_substitutes_default_governance_model():
    """Default GOVERNANCE_LLM_MODEL is the Mac Studio target (qwen3:14b)."""
    out = _run_print({"GOVERNANCE_LLM_MODEL": ""})  # force default path
    assert "qwen3:14b" in out, "default GOVERNANCE_LLM_MODEL not applied"
    assert "@GOVERNANCE_LLM_MODEL@" not in out, "placeholder still present"


def test_print_substitutes_env_override_governance_model():
    """Env-var override flows through (e.g., MacBook target qwen3:8b).

    Checks the substituted `<string>...</string>` that follows the
    GOVERNANCE_LLM_MODEL key — NOT the surrounding comment, which
    legitimately mentions both qwen3:14b and qwen3:8b as documentation.
    """
    out = _run_print({"GOVERNANCE_LLM_MODEL": "qwen3:8b"})
    # Find every <string> that directly follows a GOVERNANCE_LLM_MODEL key.
    import re
    values = re.findall(
        r"<key>GOVERNANCE_LLM_MODEL</key>\s*<string>([^<]+)</string>",
        out,
    )
    assert values, "GOVERNANCE_LLM_MODEL key/string pair not found in output"
    for v in values:
        assert v == "qwen3:8b", (
            f"GOVERNANCE_LLM_MODEL was '{v}', expected 'qwen3:8b' (env override)"
        )


def test_print_resolves_repo_root_to_current_repo():
    """Substituted REPO_ROOT must equal the actual repo root, not a stale path."""
    out = _run_print()
    assert str(REPO_ROOT) in out, f"expected REPO_ROOT={REPO_ROOT} in output"
    assert "@REPO_ROOT@" not in out, "placeholder still present"


def test_print_resolves_venv_python_path():
    """VENV_PYTHON points inside the resolved repo root."""
    out = _run_print()
    expected = str(REPO_ROOT / ".venv" / "bin" / "python")
    assert expected in out, f"expected VENV_PYTHON={expected} in output"
    assert "@VENV_PYTHON@" not in out, "placeholder still present"


def test_print_emits_both_plists():
    """Print mode must emit BOTH fast and deep plists."""
    out = _run_print()
    assert "<string>com.kalshi.governance.fast</string>" in out
    assert "<string>com.kalshi.governance.deep</string>" in out


def test_print_output_is_valid_plist_xml(tmp_path: Path):
    """plutil -lint must accept each emitted plist after substitution."""
    plutil = shutil.which("plutil")
    if plutil is None:
        pytest.skip("plutil not on PATH (non-macOS environment)")

    out = _run_print()
    # Split on the section markers the script emits.
    # Each plist starts at <?xml version="1.0" and ends at </plist>.
    # Walk the buffer and validate every plist substring independently.
    sections = out.split("<?xml version=\"1.0\" encoding=\"UTF-8\"?>")
    plists_validated = 0
    for i, section in enumerate(sections[1:], start=1):
        # Reattach the XML declaration that .split() consumed.
        plist_xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>" + section
        end_marker = "</plist>"
        end = plist_xml.find(end_marker)
        assert end != -1, f"section {i} has no closing </plist>"
        plist_xml = plist_xml[: end + len(end_marker)]

        # Write to tmp and lint.
        target = tmp_path / f"section_{i}.plist"
        target.write_text(plist_xml)
        result = subprocess.run(
            [plutil, "-lint", str(target)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"plutil -lint failed on section {i}: {result.stdout}{result.stderr}"
        )
        plists_validated += 1

    assert plists_validated == len(TEMPLATES), (
        f"expected {len(TEMPLATES)} substituted plists, validated {plists_validated}"
    )


def test_unknown_argument_is_rejected():
    """install.sh prints a usage error and exits non-zero on unknown flags."""
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--bogus-flag"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    assert "unknown argument" in (result.stderr + result.stdout).lower()
