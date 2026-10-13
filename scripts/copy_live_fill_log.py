"""Copy the gitignored live fill log onto the committed report.

The desk appends to logs/state/live-fill-log.md. Launchd runs this once a
day at 06:05 local time and copies that file onto
docs/reports/live-fill-log.md. A missing or empty source leaves the report
unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path


def copy_live_fill_log(source: Path, dest: Path) -> bool:
    """Replace dest with source. Return False when source is missing or empty."""
    if not source.is_file():
        return False
    payload = source.read_bytes()
    if not payload.strip():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(dest)
    return True


def main() -> int:
    from trading.executor import LIVE_FILL_LOG_ARCHIVE_PATH, LIVE_FILL_LOG_PATH

    copied = copy_live_fill_log(LIVE_FILL_LOG_PATH, LIVE_FILL_LOG_ARCHIVE_PATH)
    print("copied" if copied else "left-in-place")
    return 0


if __name__ == "__main__":
    sys.exit(main())
