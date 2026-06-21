"""CI wrapper for `run_replay_gate` (Phase 3 activation).

Invoked from `.github/workflows/replay-ci-gate.yml` on every PR.

Behavior:
  1. Determine changed files from `git diff <base>...HEAD`.
  2. Invoke `run_replay_gate` with classified tier + Rule 4 evaluation.
  3. Print human-readable summary + emit machine-readable artifact.
  4. Exit 0 on pass, 1 on fail (CI marks PR check accordingly).

Operator override:
  Set env var `REPLAY_GATE_OVERRIDE=1` to force exit 0 regardless of
  verdict. Documented in IC §16 §16.7 as the "operator-justified
  override" path; CI must still log the override decision.

Read-only with respect to runtime state — no DB writes, no Kalshi calls.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.edge_replay.replay_gate import run_replay_gate, GateError


def _git_changed_files(base_ref: str = "origin/main") -> list[Path]:
    """Return list of changed-file paths between `base_ref` and HEAD.

    On the merge commit (push to main), `base_ref...HEAD` is the merged
    diff. On a PR, this is the PR's full diff vs main.
    """
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            cwd=REPO_ROOT,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"replay-ci-entry: WARN — `git diff {base_ref}...HEAD` failed: {exc}", file=sys.stderr)
        print("                 Falling back to last-commit diff.", file=sys.stderr)
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD~1...HEAD"],
            cwd=REPO_ROOT,
            text=True,
        )
    return [Path(p.strip()) for p in out.splitlines() if p.strip()]


def _detect_semantic_diffs(changed: list[Path]) -> dict[str, bool | list[Path]]:
    """Derive the semantic-scope flags I-5 needs from the file list."""
    has_config_diff = any(p == Path("config.py") for p in changed)
    has_prompt_diff = any(
        p == Path("governance/prompts.py")
        or "prompt" in p.name.lower()
        for p in changed
    )
    has_model_manifest_diff = any(
        p.name in ("model_registry.json", "MODELS.md") for p in changed
    )
    schema_migrations = [
        p for p in changed if p.parent == Path("schema") or "migration" in p.name.lower()
    ]
    return {
        "config_diff": has_config_diff,
        "prompt_template_diff": has_prompt_diff,
        "model_manifest_diff": has_model_manifest_diff,
        "schema_migrations": schema_migrations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main",
                        help="Git ref to diff against (default: origin/main).")
    parser.add_argument("--commit-sha", default=None,
                        help="Override commit SHA (default: HEAD).")
    parser.add_argument("--print-only", action="store_true",
                        help="Print verdict but always exit 0 (debug mode).")
    args = parser.parse_args(argv)

    changed = _git_changed_files(args.base)
    print(f"replay-ci-entry: {len(changed)} changed files vs {args.base}")
    for p in changed[:20]:
        print(f"  - {p}")
    if len(changed) > 20:
        print(f"  ... and {len(changed) - 20} more")

    semantic = _detect_semantic_diffs(changed)
    print(f"replay-ci-entry: semantic flags: {semantic}")

    try:
        verdict = run_replay_gate(
            changed_files=changed,
            commit_sha=args.commit_sha,
            **semantic,
        )
    except GateError as exc:
        print(f"replay-ci-entry: GATE-ERROR — {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    print()
    print("=== Replay-CI Gate Verdict ===")
    print(f"  tier:    {verdict.tier}")
    print(f"  pass:    {verdict.pass_}")
    if verdict.rule4 is not None:
        print("  rule4:")
        print(f"    trade_count:  {verdict.rule4.trade_count}")
        print(f"    win_rate:     {verdict.rule4.win_rate:.3f}")
        print(f"    per_trade_ev: {verdict.rule4.per_trade_ev:+.4f}")
        print(f"    ev_ci_95_lo:  {verdict.rule4.ev_ci_95_lo:+.4f}")
        print(f"    ev_ci_95_hi:  {verdict.rule4.ev_ci_95_hi:+.4f}")
        print(f"    realized_pnl: {verdict.rule4.realized_pnl:+.4f}")
        if verdict.rule4.sharpe is not None:
            print(f"    sharpe:       {verdict.rule4.sharpe:+.4f}")
    if verdict.notes:
        print("  notes:")
        for n in verdict.notes:
            print(f"    - {n}")

    override = os.environ.get("REPLAY_GATE_OVERRIDE")
    if override == "1":
        print()
        print("replay-ci-entry: OPERATOR OVERRIDE active (REPLAY_GATE_OVERRIDE=1).")
        print("                 Forcing exit 0 regardless of verdict.")
        return 0

    if args.print_only:
        return 0
    return 0 if verdict.pass_ else 1


if __name__ == "__main__":
    sys.exit(main())
