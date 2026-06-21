"""I-5 tier classifier with semantic scope (rapid-learning framework v3).

Reference design:
    docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
    §2 (Risk Tier Matrix) and §3 row I-5.

The classifier maps a proposed change set to one of four blast-radius tiers
{T0, T1, T2, T3} so that downstream deploy gating (`replay_gate.py` + IC §16.7)
applies the correct evidence standard.

Two reduction layers, max-wins:

1. **Path-based reduction.** Each changed file is matched against the
   PATH_TIER_RULES table; the maximum tier wins. Unknown paths default to
   T3 per the §2 fail-safe rule ("when in doubt, route to T3").

2. **Semantic reduction (v3 STAMP per Codex blocker B).** Any truthy
   semantic-scope signal — config/env diff, prompt-template diff, model
   manifest diff, schema-migration list — forces T3. The classifier
   cannot currently map these to a lower tier without further design
   (per the v3 design doc), so T3 is the conservative default.

The final tier is `max(path_tier, semantic_tier)`.

------------------------------------------------------------------------------
Tier definitions (operator-readable mapping; mirrored in PATH_TIER_RULES):

- **T0 — observability / safety / mechanical.** No path to a paper or live
  trade decision changes. Examples: launchd plists, install scripts,
  governance_monitor, .githooks, docs, tests-only edits, .github workflows.
  Gate: unit tests + lint.

- **T1 — paper-mode behavioral, replay-decidable.** Touches feeds, classifier,
  blender, evidence scorer, Trade Readiness Gate G1-G6 thresholds. Effect
  on edge is measurable on existing replay corpora. Gate: replay-as-CI
  against ≥1 pre-registered regime-distinct holdout + ≥2 market families.

- **T2 — paper-mode behavioral, replay-indeterminate.** Effect depends on
  signals not present in corpora (new feeds, prompt edits). Gate: synthetic
  event corpus + cached-LLM-stub gate + 5d calendar floor.

- **T3 — live-mode / sizing / capital / runtime-infrastructure.** Kelly
  logic, bankroll mutation, hard caps, paper→live cutover, unclassified
  runtime-affecting artifacts. Gate: full IC §16 (≥30 markets, 95% CI,
  dual-agent audit, operator gate).

Path mapping below is precedence-ordered: the first matching rule wins for
a given path; cross-path max-wins still applies at the reduction layer.

This module is pure stdlib. No third-party deps.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Literal

__all__ = [
    "PATH_TIER_RULES",
    "Tier",
    "classify_tier",
    "tier_of_path",
]

Tier = Literal["T0", "T1", "T2", "T3"]

# Ordering used for max-wins reduction. Higher index == more gating.
_TIER_ORDER: tuple[Tier, ...] = ("T0", "T1", "T2", "T3")
_TIER_RANK: dict[Tier, int] = {t: i for i, t in enumerate(_TIER_ORDER)}


# ---------------------------------------------------------------------------
# Path → tier mapping
# ---------------------------------------------------------------------------
#
# Each rule is a dict with:
#   - "tier": the tier to assign on match
#   - "pattern": a human-readable description / glob-like marker
#   - "matcher": a callable (repo_relative_path: str) -> bool
#
# Rules are evaluated in declaration order; the FIRST match wins for a
# given path. Cross-path max-wins is applied at the reduction layer.
#
# Adding a rule:
#   1. Pick the right tier per §2 of the design doc.
#   2. Insert in precedence order — more specific patterns first.
#   3. Add a test in tests/test_tier_classifier.py that locks the routing
#      so an accidental reorder is caught.


def _has_segment(path: str, segment: str) -> bool:
    """True if `segment` appears as a complete path component in `path`.

    Avoids the substring-match trap where "tests" would accidentally match
    "manifests/foo.py". We normalize to forward-slash form first because
    path strings on Windows may use backslashes.
    """
    parts = path.replace("\\", "/").split("/")
    return segment in parts


def _matches_suffix(path: str, suffix: str) -> bool:
    """True if `path` ends with the given suffix (used for *.plist.template
    matches)."""
    return path.replace("\\", "/").endswith(suffix)


def _matches_prefix(path: str, prefix: str) -> bool:
    """True if the forward-slash-normalized `path` starts with `prefix`.

    Trailing slash on prefix is significant — `"trading/"` matches files
    inside the directory but not a top-level `tradingfoo.py`.
    """
    return path.replace("\\", "/").startswith(prefix)


def _eq(path: str, target: str) -> bool:
    return path.replace("\\", "/") == target


# Mapping table. Precedence: top to bottom. Rules at the top match first.
PATH_TIER_RULES: list[dict[str, Any]] = [
    # ---- T3 (highest specificity first; live-mode + sizing/capital) ----
    {
        "tier": "T3",
        "pattern": "trading/executor.py",
        "matcher": lambda p: _eq(p, "trading/executor.py"),
    },
    {
        "tier": "T3",
        "pattern": "trading/paper_trader.py",
        "matcher": lambda p: _eq(p, "trading/paper_trader.py"),
    },
    {
        "tier": "T3",
        "pattern": "config.py",
        "matcher": lambda p: _eq(p, "config.py"),
    },
    # ---- T2 (replay-indeterminate) ----
    {
        "tier": "T2",
        "pattern": "governance/prompts.py",
        "matcher": lambda p: _eq(p, "governance/prompts.py"),
    },
    # ---- T1 (paper-mode behavioral, replay-decidable) ----
    {
        "tier": "T1",
        "pattern": "analysis/market_matcher.py",
        "matcher": lambda p: _eq(p, "analysis/market_matcher.py"),
    },
    {
        "tier": "T1",
        "pattern": "analysis/signal_analyzer.py",
        "matcher": lambda p: _eq(p, "analysis/signal_analyzer.py"),
    },
    {
        # PROFIT-EDGE-014 review finding: this rule previously named
        # "analysis/blender.py", which does not exist -- the real blender is
        # analysis/decision_blender.py, so blender PRs fell through to the
        # unknown-path T3 fail-safe (same misroute class as the tasks/stats
        # false positive). The classifier docstring places the blender in T1.
        "tier": "T1",
        "pattern": "analysis/decision_blender.py",
        "matcher": lambda p: _eq(p, "analysis/decision_blender.py"),
    },
    {
        "tier": "T1",
        "pattern": "analysis/evidence_scorer.py",
        "matcher": lambda p: _eq(p, "analysis/evidence_scorer.py"),
    },
    {
        "tier": "T1",
        "pattern": "tasks/trade_readiness_gate.py",
        "matcher": lambda p: _eq(p, "tasks/trade_readiness_gate.py"),
    },
    {
        "tier": "T1",
        "pattern": "tasks/blend_task.py",
        "matcher": lambda p: _eq(p, "tasks/blend_task.py"),
    },
    {
        "tier": "T1",
        "pattern": "feeds/**",
        "matcher": lambda p: _matches_prefix(p, "feeds/"),
    },
    # ---- T0 (observability/safety/mechanical) ----
    # Tests-only edits are T0 by themselves; if paired with production-code
    # changes, max-wins routes the PR upward.
    {
        "tier": "T0",
        "pattern": "tests/**",
        "matcher": lambda p: _matches_prefix(p, "tests/"),
    },
    {
        "tier": "T0",
        "pattern": "docs/**",
        "matcher": lambda p: _matches_prefix(p, "docs/"),
    },
    {
        "tier": "T0",
        "pattern": ".github/**",
        "matcher": lambda p: _matches_prefix(p, ".github/"),
    },
    {
        "tier": "T0",
        "pattern": ".githooks/**",
        "matcher": lambda p: _matches_prefix(p, ".githooks/"),
    },
    {
        "tier": "T0",
        "pattern": ".claude/**",
        "matcher": lambda p: _matches_prefix(p, ".claude/"),
    },
    {
        "tier": "T0",
        "pattern": ".gitignore",
        "matcher": lambda p: _eq(p, ".gitignore"),
    },
    {
        "tier": "T0",
        "pattern": "scripts/launchd/**",
        "matcher": lambda p: _matches_prefix(p, "scripts/launchd/"),
    },
    {
        "tier": "T0",
        "pattern": "*.plist.template",
        "matcher": lambda p: _matches_suffix(p, ".plist.template"),
    },
    {
        "tier": "T0",
        "pattern": "scripts/bothealth.sh",
        "matcher": lambda p: _eq(p, "scripts/bothealth.sh"),
    },
    {
        "tier": "T0",
        "pattern": "scripts/governance_monitor.py",
        "matcher": lambda p: _eq(p, "scripts/governance_monitor.py"),
    },
    {
        "tier": "T0",
        "pattern": "scripts/install*.sh",
        "matcher": lambda p: (
            _matches_prefix(p, "scripts/install") and p.endswith(".sh")
        ),
    },
    {
        "tier": "T0",
        "pattern": "install.sh",
        "matcher": lambda p: _eq(p, "install.sh"),
    },
    {
        "tier": "T0",
        "pattern": "README.md",
        "matcher": lambda p: _eq(p, "README.md"),
    },
    {
        "tier": "T0",
        "pattern": "CHANGELOG.md",
        "matcher": lambda p: _eq(p, "CHANGELOG.md"),
    },
    {
        "tier": "T0",
        "pattern": "CLAUDE.md",
        "matcher": lambda p: _eq(p, "CLAUDE.md"),
    },
    {
        "tier": "T0",
        "pattern": "AGENTS.md",
        "matcher": lambda p: _eq(p, "AGENTS.md"),
    },
    {
        "tier": "T0",
        "pattern": "VERSION",
        "matcher": lambda p: _eq(p, "VERSION"),
    },
]


# Sentinel returned for "no rule matched"; the public classifier resolves
# this to T3 (fail-safe direction). Keeping the sentinel separate from "T3"
# lets the audit log distinguish "unknown path defaulted to T3" from
# "matched a T3 rule explicitly". The tighter Literal annotation makes the
# fail-safe target explicit to future readers.
_UNKNOWN_PATH: Final[Literal["T3"]] = "T3"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _normalize_path(path: str | Path, repo_root: Path | None = None) -> str:
    """Reduce `path` to a repo-relative, forward-slash string.

    Absolute paths under `repo_root` are converted to repo-relative; paths
    that are already relative are returned as-is. This is the boundary
    where str/Path differences and OS-specific separators get flattened so
    the rule matchers can do simple string comparisons.
    """
    p = Path(path) if not isinstance(path, Path) else path
    if p.is_absolute():
        root = repo_root if repo_root is not None else Path.cwd()
        try:
            p = p.relative_to(root)
        except ValueError:
            # Path is absolute but not under repo_root; treat as opaque
            # (will fall through to unknown-path default of T3).
            return p.as_posix()
    return p.as_posix()


def _match_rule(rel_path: str) -> tuple[Tier, str] | None:
    """Return (tier, pattern) for the first matching rule, or None."""
    for rule in PATH_TIER_RULES:
        if rule["matcher"](rel_path):
            return rule["tier"], rule["pattern"]
    return None


def tier_of_path(path: str | Path, repo_root: Path | None = None) -> Tier:
    """Return the tier for a single path. Unknown paths return T3.

    Accepts both str and Path inputs. Absolute paths are reduced to
    repo-relative form using `repo_root` (defaults to `Path.cwd()`).
    """
    rel = _normalize_path(path, repo_root=repo_root)
    matched = _match_rule(rel)
    if matched is None:
        return _UNKNOWN_PATH
    return matched[0]


def _max_tier(tiers: Iterable[Tier]) -> Tier:
    """Reduce an iterable of tiers by max-wins. Empty input → T0 (the
    caller treats absence of paths as a separate, fail-safe-to-T3 case at
    the classify_tier layer; this helper is just the rank reducer)."""
    best_rank = -1
    best: Tier = "T0"
    for t in tiers:
        rank = _TIER_RANK[t]
        if rank > best_rank:
            best_rank = rank
            best = t
    return best


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def _default_log_path() -> Path:
    """Resolve the audit-log path relative to the current working dir.

    We resolve at call time rather than import time so tests can monkeypatch
    chdir into a tmp directory without the module having cached the
    pre-chdir path.
    """
    return Path("logs") / "edge_replay" / "tier_classifications.jsonl"


def _now_utc_iso() -> str:
    """UTC timestamp in ISO-8601 'Z' form per ~/.claude/rules/portability.md.

    Z-suffix is load-bearing: downstream tooling parses the audit log and
    treats the absence of TZ info as "unknown" rather than UTC. We avoid
    `datetime.utcnow()` because it returns a naive datetime; the standard
    is timezone-aware UTC.
    """
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _write_audit(entry: dict[str, Any], log_path: Path | None = None) -> None:
    """Append a single JSONL line to the audit log.

    Per spec: write failures MUST NOT raise. The classifier sits on the
    deploy path; converting a log-write failure into a deploy outage would
    invert the safety priority (audit trail is desirable, gate routing is
    mandatory). On failure we emit a warning to stderr and continue.
    """
    target = log_path if log_path is not None else _default_log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception as exc:  # noqa: BLE001 — best-effort logging
        # ASCII-only per ~/.claude/rules/portability.md log-emission rule.
        sys.stderr.write(
            f"[tier_classifier] audit log write failed at {target}: {exc}\n"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_tier(
    changed_paths: Iterable[str | Path],
    config_diff: bool = False,
    prompt_template_diff: bool = False,
    model_manifest_diff: bool = False,
    schema_migrations: Iterable[str | Path] = (),
    *,
    write_ledger: bool = True,
) -> Tier:
    """Classify a change set into one of {T0, T1, T2, T3}.

    Two reduction layers, max-wins:

    1. Path-based: each entry in `changed_paths` is mapped via
       PATH_TIER_RULES; unknown paths default to T3. The maximum across
       all paths wins.

    2. Semantic-scope (v3 STAMP per Codex blocker B): if any of
       `config_diff`, `prompt_template_diff`, `model_manifest_diff`, or a
       non-empty `schema_migrations` is truthy, the change is forced to T3.

    The returned tier is `max(path_tier, semantic_tier)`. Empty inputs +
    no semantic signals default to T3 (fail-safe).

    Every call writes a JSONL audit entry to
    `logs/edge_replay/tier_classifications.jsonl` (created on first write).
    Audit-log write failures do not raise; classification result is still
    returned.

    Args:
        changed_paths: iterable of repo-relative or absolute paths.
            Strings and Path objects both accepted.
        config_diff: True if any config.py / .env / *.env edit is part of
            the change set.
        prompt_template_diff: True if any LLM prompt template change is
            part of the change set.
        model_manifest_diff: True if any model_digest pin, ollama version,
            or model-manifest change is part of the change set.
        schema_migrations: iterable of migration-file paths. Non-empty
            means a DB schema change is in scope.

    Returns:
        Literal["T0", "T1", "T2", "T3"] — the maximum tier across both
        reduction layers.
    """
    # Materialize once; do not mutate caller inputs.
    paths_list: list[str | Path] = list(changed_paths)
    migrations_list: list[str | Path] = list(schema_migrations)

    # ----- Path-based reduction -----
    per_path_results: list[tuple[str, Tier, str]] = []
    for raw in paths_list:
        rel = _normalize_path(raw)
        matched = _match_rule(rel)
        if matched is None:
            per_path_results.append((rel, _UNKNOWN_PATH, "unknown_path_default"))
        else:
            per_path_results.append((rel, matched[0], matched[1]))

    if per_path_results:
        path_tier = _max_tier(t for _, t, _ in per_path_results)
        # Identify the single (path, rule) that determined the path_tier so
        # operators can read the audit log and see WHY a tier was assigned.
        max_path_entry = max(per_path_results, key=lambda r: _TIER_RANK[r[1]])
    else:
        # No changed paths supplied. Treat as the most extreme form of doubt.
        path_tier = _UNKNOWN_PATH
        max_path_entry = ("", _UNKNOWN_PATH, "empty_changed_paths_default")

    # ----- Semantic-scope reduction -----
    semantic_triggered = (
        bool(config_diff)
        or bool(prompt_template_diff)
        or bool(model_manifest_diff)
        or bool(migrations_list)
    )
    semantic_tier: Tier = "T3" if semantic_triggered else "T0"

    final_tier: Tier = _max_tier((path_tier, semantic_tier))

    # ----- Determine rule_matched string for audit trail -----
    if semantic_triggered and _TIER_RANK[semantic_tier] >= _TIER_RANK[path_tier]:
        reasons = []
        if config_diff:
            reasons.append("config_diff")
        if prompt_template_diff:
            reasons.append("prompt_template_diff")
        if model_manifest_diff:
            reasons.append("model_manifest_diff")
        if migrations_list:
            reasons.append("schema_migrations")
        rule_matched = "semantic:" + "+".join(reasons) + "->T3"
        max_tier_path = ""
    else:
        if max_path_entry[2] in ("unknown_path_default", "empty_changed_paths_default"):
            rule_matched = f"path:{max_path_entry[0] or '<empty>'}->{max_path_entry[1]}"
        else:
            rule_matched = f"path:{max_path_entry[2]}->{max_path_entry[1]}"
        max_tier_path = max_path_entry[0]

    # ----- Audit log -----
    entry: dict[str, Any] = {
        "ts_utc": _now_utc_iso(),
        "changed_paths": [_normalize_path(p) for p in paths_list],
        "config_diff": bool(config_diff),
        "prompt_template_diff": bool(prompt_template_diff),
        "model_manifest_diff": bool(model_manifest_diff),
        "schema_migrations": [_normalize_path(p) for p in migrations_list],
        "classified_tier": final_tier,
        "max_tier_path": max_tier_path,
        "rule_matched": rule_matched,
    }
    # `write_ledger=False` lets read-only consumers (e.g. T0-budget check)
    # replay classification across historical commits without polluting the
    # runtime ledger with phantom entries indistinguishable from live
    # decisions. Default remains True so live call sites stay unchanged.
    if write_ledger:
        _write_audit(entry)

    return final_tier


# Belt-and-suspenders: importing this module must not trigger a side-effect
# classification call. The module-level smoke check below is a no-op
# placeholder so accidental "if __name__ == ..." execution at import does
# not mutate the audit log.
if __name__ == "__main__":  # pragma: no cover — manual smoke only
    print(classify_tier([], False, False, False, []))
    print("Default for empty input: T3 (fail-safe)")
    # Ensure cwd hint for the operator running this directly.
    print(f"Audit log path (cwd-relative): {_default_log_path()}")
    print(f"Current working directory: {os.getcwd()}")
