"""Report installed launchd output paths vs repo-managed template expectations."""

from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
import sys
from pathlib import Path

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from utils.output_paths import REPO_ROOT


TEMPLATE_DIR = REPO_ROOT / "ops" / "launchd"
LABELS = (
    "com.jake.kalshi-bot",
    "com.jake.kalshi-bothealth",
    "com.jake.kalshi-daily-review",
    "com.jake.kalshi-match-feedback-aggregator",
    "com.kalshi.db-backup",
    "com.kalshi.governance.fast",
    "com.kalshi.governance.deep",
)


def _render_template(label: str, repo_root: Path = REPO_ROOT) -> dict:
    path = TEMPLATE_DIR / f"{label}.plist.template"
    text = path.read_text(encoding="utf-8")
    text = text.replace("@REPO_ROOT@", str(repo_root))
    text = text.replace("@VENV_PYTHON@", str(repo_root / ".venv" / "bin" / "python"))
    text = text.replace("@GOVERNANCE_LLM_MODEL@", "qwen2.5:7b")
    return plistlib.loads(text.encode("utf-8"))


def expected_paths(repo_root: Path = REPO_ROOT) -> dict[str, dict[str, str | None]]:
    expected = {}
    for label in LABELS:
        plist = _render_template(label, repo_root)
        expected[label] = {
            "stdout": plist.get("StandardOutPath"),
            "stderr": plist.get("StandardErrorPath"),
        }
    return expected


def installed_paths(label: str, *, domain: str = "gui") -> dict[str, str | None]:
    uid = subprocess.check_output(["id", "-u"], text=True).strip()
    target = f"{domain}/{uid}/{label}" if domain == "gui" else label
    try:
        out = subprocess.check_output(
            ["/bin/launchctl", "print", target],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return {"stdout": None, "stderr": None, "error": str(exc)}

    result: dict[str, str | None] = {"stdout": None, "stderr": None}
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("stdout path = "):
            result["stdout"] = stripped.removeprefix("stdout path = ")
        elif stripped.startswith("stderr path = "):
            result["stderr"] = stripped.removeprefix("stderr path = ")
    return result


def audit(repo_root: Path = REPO_ROOT) -> list[dict[str, object]]:
    expected = expected_paths(repo_root)
    rows = []
    for label in LABELS:
        installed = installed_paths(label)
        exp = expected[label]
        rows.append(
            {
                "label": label,
                "expected": exp,
                "installed": installed,
                "matches": installed.get("stdout") == exp["stdout"]
                and installed.get("stderr") == exp["stderr"],
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    rows = audit(args.repo_root)
    print(json.dumps(rows, indent=2, sort_keys=True))
    return 0 if all(row["matches"] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
