#!/usr/bin/env python3
"""Release tag inventory plus projected Wave-1/2/3 tags."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class TagRow:
    scope: str
    tag: str
    sha: str
    subject: str


PROJECTED = (
    TagRow("projected", "v0.30.0", "TBD", "Wave-1 base-stack post-soak deploy"),
    TagRow("projected", "v0.31.0", "TBD", "Wave-2 EDGE-004 branch decision deploy"),
    TagRow("projected", "v0.32.0", "TBD", "Wave-3 escalation deploy"),
)


def _run(args: list[str]) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()


def local_tags() -> list[TagRow]:
    fmt = "%(refname:short)|%(objectname)|%(subject)"
    try:
        raw = _run(["git", "for-each-ref", "refs/tags", "--sort=-creatordate", f"--format={fmt}"])
    except subprocess.CalledProcessError:
        return []
    rows = []
    for line in raw.splitlines():
        tag, sha, subject = (line.split("|", 2) + ["", ""])[:3]
        try:
            commit_sha = _run(["git", "rev-list", "-n", "1", tag])
            commit_subject = _run(["git", "log", "-1", "--format=%s", commit_sha])
        except subprocess.CalledProcessError:
            commit_sha = sha
            commit_subject = subject
        rows.append(TagRow("local", tag, commit_sha, commit_subject))
    return rows


def print_rows(rows: list[TagRow]) -> None:
    for row in rows:
        print(f"{row.tag}|{row.subject}|{row.scope}|{row.sha}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projected-only", action="store_true")
    args = parser.parse_args()

    rows = list(PROJECTED) if args.projected_only else local_tags() + list(PROJECTED)
    print_rows(rows)
    if not args.projected_only:
        print("verify: git fetch --tags origin && git tag --list 'v0.*' --sort=-v:refname")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
