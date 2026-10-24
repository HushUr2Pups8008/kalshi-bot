#!/usr/bin/env python3
"""Size-based copytruncate for launchd stdout and stderr files.

Launchd keeps the log file open. Renaming it would leave the process
writing to the old inode, so this copies the bytes aside and truncates
the live file. Five archives are kept.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
KEEP = 5


def rotate_file(path: Path, *, max_bytes: int = MAX_BYTES, keep: int = KEEP) -> bool:
    """Copy and truncate path when it is larger than max_bytes. Keep archives."""
    if keep < 1:
        raise ValueError("keep")
    if max_bytes < 1:
        raise ValueError("max_bytes")
    if not path.is_file():
        return False
    if path.stat().st_size <= max_bytes:
        return False
    for index in range(keep, 1, -1):
        older = Path(f"{path}.{index}")
        newer = Path(f"{path}.{index - 1}")
        if index == keep and older.exists():
            older.unlink()
        if newer.is_file():
            newer.replace(older)
    shutil.copyfile(path, Path(f"{path}.1"))
    with path.open("r+b") as handle:
        handle.truncate(0)
    return True


def main(argv: list[str]) -> int:
    for raw in argv:
        try:
            rotate_file(Path(raw))
        except OSError as exc:
            print(
                f"launchd log rotate failed name={Path(raw).name} error_type={type(exc).__name__}",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
