#!/usr/bin/env python3
"""Sync the README version badge and "Current through" line to VERSION.

Source of truth:
- `VERSION` file at repo root → version string

Targets in `README.md`:
- The shields.io "version-X.Y.Z-blue" badge URL
- The "Current through vX.Y.Z" entry in the release-history table row

Note on the tests-count badge: deliberately NOT included. macOS-only
tests (`test_botcheck.py`, `test_launchd_install_script.py`) are skipped
on Linux CI, so the collected count differs by platform — auto-syncing
it would cause CI ↔ local drift on every push. Update that badge by
hand when the cross-platform count meaningfully changes.

Modes:
  --check   Exit 0 if README is in sync, exit 1 with diff hint if not.
  --write   Rewrite README in place. (Default if no flag given.)

This is the load-bearing half of the version-bump workflow. Pre-commit
hook fires `--write` whenever VERSION is staged; CI fires `--check` on
every push so a hand-edit that drifts is caught before merge.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "VERSION"
README_FILE = REPO_ROOT / "README.md"

VERSION_BADGE_RE = re.compile(
    r"(https://img\.shields\.io/badge/version-)([0-9][0-9A-Za-z\.\-]+)(-blue\))"
)
CURRENT_THROUGH_RE = re.compile(
    r"(\| Current through )v[0-9][0-9A-Za-z\.\-]+( \|)"
)


def read_version() -> str:
    return VERSION_FILE.read_text().strip()


def rewrite(readme: str, version: str) -> str:
    new = VERSION_BADGE_RE.sub(rf"\g<1>{version}\g<3>", readme)
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
    readme = README_FILE.read_text()
    new = rewrite(readme, version)

    if args.check:
        if new == readme:
            print(f"[sync_readme_version] OK — README in sync with VERSION={version}")
            return 0
        print(f"[sync_readme_version] DRIFT — README does not match VERSION={version}")
        print("[sync_readme_version] Run: scripts/sync_readme_version.py --write")
        return 1

    if new == readme:
        print(f"[sync_readme_version] No-op — already in sync (VERSION={version}).")
        return 0
    README_FILE.write_text(new)
    print(f"[sync_readme_version] Rewrote README → version={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
