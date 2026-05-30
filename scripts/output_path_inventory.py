"""Inventory output-path literals and flag unclassified writers.

This is a guardrail, not a formatter. It intentionally allows documented
legacy read paths while flagging new ad-hoc output roots such as
``analysis_outputs/``.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
_STRING_RE = re.compile(r"""["']([^"']*(?:logs|data|reports|analysis_outputs)[^"']*)["']""")
_SCAN_SUFFIXES = {".py", ".sh", ".toml", ".yaml", ".yml", ".json", ".plist", ".template"}
_IGNORE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "logs",
    "data",
    "htmlcov",
    "recovered_artifacts",
    "docs",
    "tests",
    "windows_archive",
}
_LEGACY_READ_LITERALS = {
    "logs/trades/trades.jsonl",
}


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    literal: str
    classification: str


def classify_literal(literal: str) -> str:
    normalized = literal.replace("\\", "/")
    if normalized.startswith(("http://", "https://", "/private/tmp/")):
        return "non-path"
    if normalized.startswith("${KALSHI_OUTPUT_ROOT"):
        return "raw"
    if normalized.startswith("HEAD:data/"):
        return "state"
    if normalized.startswith("$OUT_DIR/") or "replay_dataset.jsonl" in normalized:
        return "derived"
    if "logs_" in normalized and normalized.endswith(".tar.gz"):
        return "backup"
    if normalized == "logs/**/*.jsonl":
        return "raw"
    if any(ch.isspace() for ch in normalized):
        return "non-path"
    if normalized in {"logs", "logs/*"}:
        return "raw"
    if normalized == "data":
        return "state"
    if normalized == "reports":
        return "report"
    if "/" not in normalized:
        return "non-path"
    def has(segment: str) -> bool:
        return normalized.startswith(segment) or f"/{segment}" in normalized

    if normalized in _LEGACY_READ_LITERALS:
        return "legacy-read"
    if "analysis_outputs/" in normalized or normalized.startswith("reports/"):
        return "unclassified"
    if has("logs/app"):
        return "raw"
    if (
        has("logs/trades")
        or normalized == "logs/trades"
    ):
        return "raw"
    if has("logs/governance"):
        return "raw"
    if has("logs/edge_replay"):
        return "raw"
    if has("logs/tests"):
        return "raw"
    if has("logs/reports") or has("reports/"):
        return "report"
    if has("logs/state/derived") or has("state/derived"):
        return "derived"
    if has("logs/backups") or has("backups/"):
        return "backup"
    if "/data/" in normalized or normalized.startswith("data/"):
        return "state"
    return "unclassified"


def scan_file(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    if path.resolve() == Path(__file__).resolve():
        return findings
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []
    for line_no, line in enumerate(lines, start=1):
        for match in _STRING_RE.finditer(line):
            literal = match.group(1)
            classification = classify_literal(literal)
            if classification == "non-path":
                continue
            if classification == "unclassified":
                findings.append(Finding(path, line_no, literal, classification))
    return findings


def iter_source_files(root: Path = REPO_ROOT):
    for path in root.rglob("*"):
        if any(part in _IGNORE_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file() and (path.suffix in _SCAN_SUFFIXES or path.name == "Makefile"):
            yield path


def scan_repo(root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_source_files(root):
        findings.extend(scan_file(path))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find unclassified output-path literals")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    findings = scan_repo(args.root)
    for finding in findings:
        rel = finding.path.relative_to(args.root)
        print(f"{rel}:{finding.line}: {finding.literal} [{finding.classification}]")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
