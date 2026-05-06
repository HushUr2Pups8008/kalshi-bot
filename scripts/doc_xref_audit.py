#!/usr/bin/env python3
"""Find dead relative Markdown links under docs/.

Scope: `[text](path)` form only. Backtick-formatted code spans are NOT
audited because they're used for inline code formatting (e.g.
`` `dossier_builder.py` ``) far more often than for hyperlinks; treating
backticks as link candidates produces overwhelming false-positive noise
(cycle-9 triage: 121 backtick "broken refs" → 0 real breakage).

Block-level skip: any region between `<!-- audit-skip-block: <reason> -->`
and `<!-- /audit-skip-block -->` HTML comments is excluded. Use this for
prestaged-changelog blocks where links are repo-root-relative for the
PASTE-TARGET file (e.g. `CHANGELOG.md` at repo root), not for the source
file the audit reads.

Per-link skip: the link's URL portion may include `<!-- audit-skip -->`
to skip a single link.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote


LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
SKIP_BLOCK_OPEN_RE = re.compile(r"<!--\s*audit-skip-block(?::[^>]*)?\s*-->")
SKIP_BLOCK_CLOSE_RE = re.compile(r"<!--\s*/audit-skip-block\s*-->")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")
PLACEHOLDER_TARGETS = {"...", "path", "*.md", "<path>", "TBD"}


def _target_exists(source: Path, raw_target: str, repo_root: Path) -> bool:
    target = raw_target.split("#", 1)[0].strip()
    if not target or target.startswith(SKIP_PREFIXES):
        return True
    if target in PLACEHOLDER_TARGETS:
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


def audit(repo_root: Path, include_archive: bool = False) -> list[str]:
    failures: list[str] = []
    docs_root = repo_root / "docs"
    for source in sorted(docs_root.rglob("*.md")):
        if not include_archive and "_archive" in source.parts:
            continue
        text = source.read_text(errors="replace")
        in_skip_block = False
        for line_no, line in enumerate(text.splitlines(), start=1):
            if SKIP_BLOCK_OPEN_RE.search(line):
                in_skip_block = True
                continue
            if SKIP_BLOCK_CLOSE_RE.search(line):
                in_skip_block = False
                continue
            if in_skip_block:
                continue
            scrubbed = INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)
            for match in LINK_RE.finditer(scrubbed):
                target = match.group(1).strip()
                if not _target_exists(source, target, repo_root):
                    rel = source.relative_to(repo_root)
                    failures.append(f"{rel}:{line_no}: dead link: {target}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--include-archive",
        action="store_true",
        help="Include docs/_archive/ in the audit (default: skip; archive is non-load-bearing per cycle-2 doc-index-audit)",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    failures = audit(repo_root, include_archive=args.include_archive)
    for failure in failures:
        print(failure)
    print(f"checked docs markdown links: dead={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
