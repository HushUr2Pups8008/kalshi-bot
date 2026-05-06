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

Fenced code blocks (``` or ~~~) are excluded — content inside fences is
code formatting, not hyperlink markup. Inline code spans (`...`) are
also stripped before link match for the same reason.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote


LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SKIP_BLOCK_OPEN_RE = re.compile(r"<!--\s*audit-skip-block(?::[^>]*)?\s*-->")
SKIP_BLOCK_CLOSE_RE = re.compile(r"<!--\s*/audit-skip-block\s*-->")
CHANGELOG_HEADING_VERSION_RE = re.compile(r"^##\s+\[?v?(\d+\.\d+\.\d+)\]?")
ECHO_VERSION_RE = re.compile(r'echo\s+["\'](\d+\.\d+\.\d+)["\']')
GIT_TAG_VERSION_RE = re.compile(r"git\s+tag\s+-a\s+v(\d+\.\d+\.\d+)\b")
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
        skip_block_start = 0
        skip_block_lines: list[tuple[int, str]] = []
        in_fence = False
        for line_no, line in enumerate(text.splitlines(), start=1):
            if SKIP_BLOCK_OPEN_RE.search(line):
                in_skip_block = True
                skip_block_start = line_no
                skip_block_lines = []
                continue
            if SKIP_BLOCK_CLOSE_RE.search(line):
                failures.extend(_audit_skip_block_versions(source, repo_root, skip_block_start, skip_block_lines))
                in_skip_block = False
                continue
            if in_skip_block:
                skip_block_lines.append((line_no, line))
                continue
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            scrubbed = INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)
            for match in LINK_RE.finditer(scrubbed):
                target = match.group(1).strip()
                if not _target_exists(source, target, repo_root):
                    rel = source.relative_to(repo_root)
                    failures.append(f"{rel}:{line_no}: dead link: {target}")
    return failures


def _audit_skip_block_versions(
    source: Path,
    repo_root: Path,
    start_line: int,
    lines: list[tuple[int, str]],
) -> list[str]:
    text = "\n".join(line for _line_no, line in lines)
    if "```" not in text and "~~~" not in text:
        return []

    versions: list[tuple[str, str, int]] = []
    for line_no, line in lines:
        for label, regex in (
            ("heading", CHANGELOG_HEADING_VERSION_RE),
            ("echo", ECHO_VERSION_RE),
            ("git_tag", GIT_TAG_VERSION_RE),
        ):
            match = regex.search(line)
            if match:
                versions.append((label, match.group(1), line_no))

    if len({version for _label, version, _line_no in versions}) <= 1:
        return []

    rel = source.relative_to(repo_root)
    detail = ", ".join(f"{label}={version}@{line_no}" for label, version, line_no in versions)
    return [f"{rel}:{start_line}: version drift in audit-skip-block: {detail}"]


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
