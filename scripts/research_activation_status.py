#!/usr/bin/env python3
"""Read-only verifier for research activation profiles.

This script compares a committed profile file against the active `.env` without
mutating config, restarting services, writing databases, or calling external
APIs.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ResearchActivationAssessment:
    ok: bool
    profile_path: Path
    env_path: Path
    missing: list[str] = field(default_factory=list)
    mismatched: list[tuple[str, str, str]] = field(default_factory=list)
    unsafe: list[str] = field(default_factory=list)
    profile_values: dict[str, str] = field(default_factory=dict)
    env_values: dict[str, str] = field(default_factory=dict)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def evaluate_activation_profile(
    repo_root: Path,
    profile_path: Path,
    *,
    env_path: Path | None = None,
) -> ResearchActivationAssessment:
    repo_root = repo_root.resolve()
    profile_path = profile_path if profile_path.is_absolute() else repo_root / profile_path
    env_path = env_path or repo_root / ".env"
    env_path = env_path if env_path.is_absolute() else repo_root / env_path

    profile_values = _parse_env_file(profile_path)
    env_values = _parse_env_file(env_path)
    missing: list[str] = []
    mismatched: list[tuple[str, str, str]] = []
    unsafe: list[str] = []

    for key, expected in profile_values.items():
        actual = env_values.get(key)
        if actual is None:
            missing.append(key)
        elif actual != expected:
            mismatched.append((key, expected, actual))

    if env_values.get("LIVE_TRADING_ENABLED", "").strip().lower() == "true":
        unsafe.append(
            "LIVE_TRADING_ENABLED=true is not allowed for research activation profiles"
        )

    return ResearchActivationAssessment(
        ok=not missing and not mismatched and not unsafe,
        profile_path=profile_path,
        env_path=env_path,
        missing=missing,
        mismatched=mismatched,
        unsafe=unsafe,
        profile_values=profile_values,
        env_values=env_values,
    )


def _default_repo_root() -> Path:
    return Path(os.environ.get("KALSHI_HOME", Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_home = _default_repo_root()
    parser.add_argument("--home", type=Path, default=default_home)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("docs/governance/research-shadow.env.example"),
        help="Activation profile to compare against .env.",
    )
    parser.add_argument("--env", type=Path, default=None, help="Env file to inspect.")
    args = parser.parse_args()

    assessment = evaluate_activation_profile(
        args.home,
        args.profile,
        env_path=args.env,
    )

    print(f"Research activation status: {'PASS' if assessment.ok else 'FAIL'}")
    print(f"profile: {assessment.profile_path}")
    print(f"env: {assessment.env_path}")
    if assessment.missing:
        print("missing:")
        for key in assessment.missing:
            expected = assessment.profile_values.get(key, "")
            print(f"- {key}={expected}")
    if assessment.mismatched:
        print("mismatched:")
        for key, expected, actual in assessment.mismatched:
            print(f"- {key}: expected={expected} actual={actual}")
    if assessment.unsafe:
        print("unsafe:")
        for item in assessment.unsafe:
            print(f"- {item}")
    if not assessment.ok:
        print("required profile lines:")
        for key, value in assessment.profile_values.items():
            print(f"{key}={value}")
    return 0 if assessment.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
