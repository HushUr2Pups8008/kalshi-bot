#!/usr/bin/env python3
"""Sync the README version + test-count badges and "Current through"
line to the canonical sources of truth.

Sources of truth:
- `VERSION` file at repo root → version string
- `pytest --collect-only -q` → test-collection count

Targets in `README.md`:
- The shields.io "version-X.Y.Z-blue" badge URL
- The shields.io "tests-N%20passing-brightgreen" badge URL
- The "Current through vX.Y.Z" entry in the release-history table row

Modes:
  --check   Exit 0 if README is in sync, exit 1 with diff hint if not.
  --write   Rewrite README in place. (Default if no flag given.)

Reasoning: this is the load-bearing half of the version-bump workflow.
Pre-commit hook fires `--write` whenever VERSION is staged; CI fires
`--check` on every push so a hand-edit that drifts is caught before merge.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "VERSION"
README_FILE = REPO_ROOT / "README.md"

VERSION_BADGE_RE = re.compile(
    r"(https://img\.shields\.io/badge/version-)([0-9][0-9A-Za-z\.\-]+)(-blue\))"
)
TESTS_BADGE_RE = re.compile(
    r"(https://img\.shields\.io/badge/tests-)(\d+)(%20passing-brightgreen\))"
)
CURRENT_THROUGH_RE = re.compile(
    r"(\| Current through )v[0-9][0-9A-Za-z\.\-]+( \|)"
)


def read_version() -> str:
    return VERSION_FILE.read_text().strip()


def collect_test_count() -> int:
    """Return the pytest-collected test count.

    Falls back to None if pytest can't run (e.g. CI lint stage with no
    test deps yet); caller decides whether to error or skip the test
    badge update.
    """
    try:
        result = subprocess.run(
            [".venv/bin/python", "-m", "pytest", "--collect-only", "-q"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1
    # Last non-empty line of stdout looks like: "1424 tests collected in 0.60s"
    for line in reversed(result.stdout.strip().splitlines()):
        m = re.match(r"(\d+) tests? collected", line.strip())
        if m:
            return int(m.group(1))
    return -1


def rewrite(readme: str, version: str, test_count: int) -> str:
    new = VERSION_BADGE_RE.sub(rf"\g<1>{version}\g<3>", readme)
    if test_count > 0:
        new = TESTS_BADGE_RE.sub(rf"\g<1>{test_count}\g<3>", new)
    new = CURRENT_THROUGH_RE.sub(rf"\g<1>v{version}\g<2>", new)
    return new


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit non-zero on drift")
    mode.add_argument("--write", action="store_true", help="rewrite README in place")
    args = parser.parse_args()

    if not args.check and not args.write:
        args.write = True

    version = read_version()
    test_count = collect_test_count()
    readme = README_FILE.read_text()
    new = rewrite(readme, version, test_count)

    if args.check:
        if new == readme:
            print(f"[sync_readme_version] OK — README in sync with VERSION={version}")
            if test_count > 0:
                print(f"[sync_readme_version]    tests badge OK ({test_count})")
            return 0
        print(
            "[sync_readme_version] DRIFT — README does not match VERSION="
            f"{version}"
            + (f" / tests={test_count}" if test_count > 0 else "")
        )
        print("[sync_readme_version] Run: scripts/sync_readme_version.py --write")
        return 1

    if new == readme:
        print(f"[sync_readme_version] No-op — already in sync (VERSION={version}).")
        return 0
    README_FILE.write_text(new)
    print(
        f"[sync_readme_version] Rewrote README → version={version}"
        + (f", tests={test_count}" if test_count > 0 else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
