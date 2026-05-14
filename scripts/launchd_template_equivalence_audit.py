#!/usr/bin/env python3
"""Audit launchd templates against installed plists and captured fixtures."""

from __future__ import annotations

import argparse
import difflib
import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_LABELS = (
    "com.jake.kalshi-bot",
    "com.jake.kalshi-bothealth",
    "com.jake.kalshi-daily-review",
    "com.jake.kalshi-soak-check",
    "com.kalshi.db-backup",
    "com.kalshi.governance.fast",
    "com.kalshi.governance.deep",
)


@dataclass(frozen=True)
class Paths:
    repo_root: Path
    venv_python: Path
    template_dir: Path
    installed_dir: Path
    fixtures_dir: Path
    governance_model: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def render_template(label: str, paths: Paths) -> str:
    template = paths.template_dir / f"{label}.plist.template"
    text = template.read_text()
    return (
        text.replace("@REPO_ROOT@", str(paths.repo_root))
        .replace("@VENV_PYTHON@", str(paths.venv_python))
        .replace("@GOVERNANCE_LLM_MODEL@", paths.governance_model)
    )


def normalize(text: str, paths: Paths) -> str:
    return (
        text.replace(str(paths.venv_python), "@VENV_PYTHON@")
        .replace(str(paths.repo_root), "@REPO_ROOT@")
        .replace(paths.governance_model, "@GOVERNANCE_LLM_MODEL@")
    )


def unified_diff(expected: str, actual: str, expected_name: str, actual_name: str) -> str:
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=expected_name,
            tofile=actual_name,
        )
    )


def compare_texts(label: str, candidate_name: str, candidate: str | None, rendered: str, paths: Paths) -> dict:
    if candidate is None:
        return {"label": label, "target": candidate_name, "status": "missing", "diff": ""}

    expected = normalize(rendered, paths)
    actual = normalize(candidate, paths)
    if actual == expected:
        return {"label": label, "target": candidate_name, "status": "match", "diff": ""}

    return {
        "label": label,
        "target": candidate_name,
        "status": "drift",
        "diff": unified_diff(expected, actual, f"rendered/{label}.plist", f"{candidate_name}/{label}.plist"),
    }


def load_optional(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text()


def audit(labels: tuple[str, ...], paths: Paths, include_installed: bool, include_fixtures: bool) -> list[dict]:
    rows: list[dict] = []
    for label in labels:
        rendered = render_template(label, paths)
        if include_installed:
            installed = load_optional(paths.installed_dir / f"{label}.plist")
            rows.append(compare_texts(label, "installed", installed, rendered, paths))
        if include_fixtures:
            fixture = load_optional(paths.fixtures_dir / f"{label}.plist")
            rows.append(compare_texts(label, "fixture", fixture, rendered, paths))
    return rows


def main() -> int:
    repo_root = _repo_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed-dir", default=Path.home() / "Library/LaunchAgents")
    parser.add_argument("--fixtures-dir", default=repo_root / "tests/fixtures/installed_plists")
    parser.add_argument("--governance-model", default=os.environ.get("GOVERNANCE_LLM_MODEL", "qwen3:14b"))
    parser.add_argument("--labels", nargs="*", default=list(DEFAULT_LABELS))
    parser.add_argument("--installed", action="store_true", help="compare against installed plists")
    parser.add_argument("--fixtures", action="store_true", help="compare against canonical fixture plists")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    include_installed = args.installed or not args.fixtures
    include_fixtures = args.fixtures
    paths = Paths(
        repo_root=repo_root,
        venv_python=repo_root / ".venv/bin/python",
        template_dir=repo_root / "ops/launchd",
        installed_dir=Path(args.installed_dir),
        fixtures_dir=Path(args.fixtures_dir),
        governance_model=args.governance_model,
    )
    rows = audit(tuple(args.labels), paths, include_installed, include_fixtures)
    status = "pass" if all(row["status"] == "match" for row in rows) else "fail"

    if args.json:
        print(json.dumps({"status": status, "checks": rows}, separators=(",", ":")))
    else:
        print("# launchd template equivalence audit")
        print()
        print(f"Status: {status}")
        print()
        for row in rows:
            print(f"- {row['label']} {row['target']}: {row['status']}")
            if row["diff"]:
                print()
                print("```diff")
                print(row["diff"].rstrip())
                print("```")
                print()

    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
