#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    proc = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/python"),
            str(REPO_ROOT / "scripts/launchd_template_equivalence_audit.py"),
            "--installed",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if not proc.stdout.strip():
        sys.stderr.write(proc.stderr)
        return proc.returncode or 1

    payload = json.loads(proc.stdout)
    print("# launchd template drift report")
    print()
    print(f"Status: {payload['status']}")
    print()
    print("| plist | target | status | operator action |")
    print("|---|---|---|---|")
    for row in payload["checks"]:
        action = "none" if row["status"] == "match" else "review diff before install"
        print(f"| `{row['label']}` | {row['target']} | {row['status']} | {action} |")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
