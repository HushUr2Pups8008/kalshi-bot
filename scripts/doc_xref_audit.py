#!/usr/bin/env python3
"""Find dead relative Markdown links under docs/."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote


LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


def _target_exists(source: Path, raw_target: str, repo_root: Path) -> bool:
    target = raw_target.split("#", 1)[0].strip()
    if not target or target.startswith(SKIP_PREFIXES):
        return True
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = unquote(target)
    path = (source.parent / target).resolve()
    try:
        path.relative_to(repo_root)
    except ValueError:
        return False
    return path.exists()


def audit(repo_root: Path) -> list[str]:
    failures: list[str] = []
    docs_root = repo_root / "docs"
    for source in sorted(docs_root.rglob("*.md")):
        text = source.read_text(errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in LINK_RE.finditer(line):
                target = match.group(1).strip()
                if not _target_exists(source, target, repo_root):
                    rel = source.relative_to(repo_root)
                    failures.append(f"{rel}:{line_no}: dead link: {target}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    failures = audit(repo_root)
    for failure in failures:
        print(failure)
    print(f"checked docs markdown links: dead={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
