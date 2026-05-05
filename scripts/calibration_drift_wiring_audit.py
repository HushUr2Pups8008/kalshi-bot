"""Audit CALIBRATION_CHECK consumer wiring before Wave-1/Wave-2 deploys."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def analyze(repo_root: Path = _REPO_ROOT) -> dict[str, Any]:
    main_py = _read(repo_root / "main.py")
    calibration_task = _read(repo_root / "tasks/calibration_task.py")
    blend_task = _read(repo_root / "tasks/blend_task.py")

    checks = [
        _check(
            "main_imports_calibration_task",
            "from tasks.calibration_task import CalibrationTask" in main_py,
            "main.py imports CalibrationTask",
        ),
        _check(
            "main_constructs_single_calibration_task",
            "self._calibration_task = CalibrationTask()" in main_py,
            "TradingBot constructs a shared CalibrationTask instance",
        ),
        _check(
            "paper_trader_receives_calibration_task",
            "calibration_task=self._calibration_task" in main_py,
            "PaperTrader receives the shared CalibrationTask for CALIBRATION_CHECK emission",
        ),
        _check(
            "blend_task_receives_calibration",
            "calibration=self._calibration_task" in main_py,
            "BlendTask receives the same CalibrationTask for confidence scaling",
        ),
        _check(
            "calibration_task_records_checks",
            "record_calibration_check" in calibration_task,
            "CalibrationTask exposes record_calibration_check",
        ),
        _check(
            "blend_task_consumes_scaling_factor",
            "get_scaling_factor" in blend_task and "calibration" in blend_task,
            "BlendTask keeps calibration scaling hook wired",
        ),
    ]
    status = "pass" if all(check["passed"] for check in checks) else "fail"
    return {
        "repo_root": str(repo_root),
        "status": status,
        "checks": checks,
    }


def render_markdown(report: dict[str, Any]) -> str:
    rows = ["| check | status | detail |", "|---|---|---|"]
    for check in report["checks"]:
        rows.append(
            f"| {check['name']} | {'PASS' if check['passed'] else 'FAIL'} | {check['detail']} |"
        )
    return "\n".join([
        "# Calibration Drift Wiring Audit",
        "",
        f"Repo: `{report['repo_root']}`",
        f"Status: `{report['status']}`",
        "",
        "## Checks",
        "",
        "\n".join(rows),
    ]) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = analyze(args.repo_root)
    if args.json:
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    else:
        sys.stdout.write(render_markdown(report))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
