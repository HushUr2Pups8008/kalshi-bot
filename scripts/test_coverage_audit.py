"""Line-coverage audit wrapper for the current test corpus."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent


def analyze_coverage_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    modules = []
    for file_path, row in sorted((payload.get("files") or {}).items()):
        summary = row.get("summary") or {}
        modules.append({
            "path": file_path,
            "covered_lines": summary.get("covered_lines", 0),
            "num_statements": summary.get("num_statements", 0),
            "percent_covered": round(float(summary.get("percent_covered", 0.0)), 2),
        })
    modules.sort(key=lambda row: (row["percent_covered"], row["path"]))
    totals = payload.get("totals") or {}
    return {
        "coverage_json": str(path),
        "totals": {
            "covered_lines": totals.get("covered_lines", 0),
            "num_statements": totals.get("num_statements", 0),
            "percent_covered": round(float(totals.get("percent_covered", 0.0)), 2),
        },
        "modules": modules,
    }


def run_pytest_coverage(repo_root: Path, pytest_args: list[str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="coverage-audit-") as tmp:
        coverage_json = Path(tmp) / "coverage.json"
        python = repo_root / ".venv/bin/python"
        if not python.exists():
            python = Path(sys.executable)
        cmd = [
            str(python),
            "-m",
            "pytest",
            "--cov=.",
            f"--cov-report=json:{coverage_json}",
            "--cov-report=term-missing:skip-covered",
            *pytest_args,
        ]
        completed = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        report = analyze_coverage_json(coverage_json) if coverage_json.exists() else {
            "coverage_json": str(coverage_json),
            "totals": {"covered_lines": 0, "num_statements": 0, "percent_covered": 0.0},
            "modules": [],
        }
        report["pytest_returncode"] = completed.returncode
        report["pytest_command"] = cmd
        report["pytest_tail"] = "\n".join((completed.stdout + completed.stderr).splitlines()[-40:])
        return report


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    rows = ["| module | covered | statements | percent |", "|---|---:|---:|---:|"]
    for row in report["modules"][:50]:
        rows.append(
            f"| `{row['path']}` | {row['covered_lines']} | "
            f"{row['num_statements']} | {row['percent_covered']}% |"
        )
    return "\n".join([
        "# Test Coverage Audit",
        "",
        "## Summary",
        "",
        f"- Covered lines: `{totals['covered_lines']}`",
        f"- Statements: `{totals['num_statements']}`",
        f"- Line coverage: `{totals['percent_covered']}%`",
        f"- Pytest returncode: `{report.get('pytest_returncode', 'not-run')}`",
        "",
        "## Lowest-Covered Modules",
        "",
        "\n".join(rows),
    ]) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--coverage-json", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if args.coverage_json:
        report = analyze_coverage_json(args.coverage_json)
    else:
        pytest_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
        report = run_pytest_coverage(args.repo_root, pytest_args)
    if args.json:
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    else:
        sys.stdout.write(render_markdown(report))
    return int(report.get("pytest_returncode", 0))


if __name__ == "__main__":
    raise SystemExit(main())
