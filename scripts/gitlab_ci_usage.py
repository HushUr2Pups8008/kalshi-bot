#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse


PLAN_LIMITS = {
    "free": 400,
    "premium": 10_000,
    "ultimate": 50_000,
}


class GitLabUsageError(RuntimeError):
    pass


def run_glab(args: list[str]) -> dict:
    try:
        result = subprocess.run(
            ["glab", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise GitLabUsageError("glab not found; install/authenticate GitLab CLI first") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitLabUsageError("glab command timed out") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitLabUsageError(detail or f"glab exited {result.returncode}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GitLabUsageError("glab returned non-JSON output") from exc


def run_git(args: list[str], repo_root: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitLabUsageError(detail or f"git exited {result.returncode}")
    return result.stdout.strip()


def namespace_from_remote(remote_url: str) -> str:
    remote_url = remote_url.strip()
    if remote_url.startswith("git@"):
        path = remote_url.split(":", 1)[1]
    else:
        parsed = urlparse(remote_url)
        path = parsed.path.lstrip("/")

    if not path:
        raise GitLabUsageError(f"could not parse namespace from remote: {remote_url}")

    first_segment = path.split("/", 1)[0].removesuffix(".git")
    if not first_segment:
        raise GitLabUsageError(f"could not parse namespace from remote: {remote_url}")
    return first_segment


def default_namespace(repo_root: Path) -> str:
    return namespace_from_remote(run_git(["remote", "get-url", "origin"], repo_root))


def month_start(raw: str | None) -> str:
    if raw:
        try:
            parsed = datetime.strptime(raw, "%Y-%m").date()
        except ValueError as exc:
            raise GitLabUsageError("--month must use YYYY-MM") from exc
        return parsed.replace(day=1).isoformat()
    today = date.today()
    return today.replace(day=1).isoformat()


def namespace_gid(namespace: dict) -> str:
    namespace_id = namespace.get("id")
    kind = namespace.get("kind")
    if not namespace_id or not kind:
        raise GitLabUsageError("namespace response missing id/kind")
    if kind == "user":
        return f"gid://gitlab/Namespaces::UserNamespace/{namespace_id}"
    if kind == "group":
        return f"gid://gitlab/Namespaces::GroupNamespace/{namespace_id}"
    raise GitLabUsageError(f"unsupported namespace kind: {kind}")


def get_namespace(namespace: str) -> dict:
    return run_glab(["api", f"namespaces/{quote(namespace, safe='')}"])


def get_usage_nodes(namespace: dict, usage_month: str) -> list[dict]:
    query = (
        "query { "
        f'ciMinutesUsage(namespaceId: "{namespace_gid(namespace)}", date: "{usage_month}", first: 1) '
        "{ nodes { minutes sharedRunnersDuration } } "
        "}"
    )
    payload = run_glab(["api", "graphql", "-f", f"query={query}"])
    errors = payload.get("errors")
    if errors:
        message = errors[0].get("message", "GraphQL usage query failed")
        raise GitLabUsageError(message)
    return payload.get("data", {}).get("ciMinutesUsage", {}).get("nodes", []) or []


def summarize_usage(namespace: dict, nodes: list[dict], usage_month: str, limit_override: int | None = None) -> dict:
    node = nodes[0] if nodes else {}
    used = int(round(float(node.get("minutes") or 0)))
    runner_seconds = int(node.get("sharedRunnersDuration") or 0)
    plan = str(namespace.get("plan") or "unknown").lower()
    limit = limit_override if limit_override is not None else PLAN_LIMITS.get(plan)
    remaining = None if limit is None else max(limit - used, 0)
    used_fraction = None if not limit else round(used / limit, 4)

    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "namespace": namespace.get("full_path") or namespace.get("path"),
        "namespace_kind": namespace.get("kind"),
        "plan": plan,
        "month": usage_month[:7],
        "used_minutes": used,
        "limit_minutes": limit,
        "remaining_minutes": remaining,
        "used_fraction": used_fraction,
        "used_percent": None if used_fraction is None else round(used_fraction * 100, 1),
        "remaining_percent": None if used_fraction is None else round((1 - used_fraction) * 100, 1),
        "runner_duration_seconds": runner_seconds,
        "runner_duration_minutes": round(runner_seconds / 60, 1),
    }


def print_human(summary: dict) -> None:
    print("# GitLab CI/CD compute usage")
    print()
    print(f"Namespace: {summary['namespace']}")
    print(f"Plan: {summary['plan']}")
    print(f"Month: {summary['month']}")
    if summary["limit_minutes"] is None:
        print(f"Used: {summary['used_minutes']} compute minutes")
        print("Limit: unknown for plan")
    else:
        print(
            "Used: "
            f"{summary['used_minutes']} / {summary['limit_minutes']} compute minutes "
            f"({summary['used_percent']:.1f}%)"
        )
        print(f"Remaining: {summary['remaining_minutes']} compute minutes ({summary['remaining_percent']:.1f}%)")
    print(
        "Runner duration: "
        f"{summary['runner_duration_seconds']}s "
        f"({summary['runner_duration_minutes']:.1f} runner minutes)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Report GitLab.com CI/CD compute-minute usage for this namespace.")
    parser.add_argument("--namespace", help="Top-level GitLab namespace. Defaults to origin remote namespace.")
    parser.add_argument("--month", help="Usage month as YYYY-MM. Defaults to current month.")
    parser.add_argument("--limit", type=int, help="Override monthly compute-minute limit.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    try:
        repo_root = Path(run_git(["rev-parse", "--show-toplevel"], Path.cwd()))
        namespace_name = args.namespace or default_namespace(repo_root)
        usage_month = month_start(args.month)
        namespace = get_namespace(namespace_name)
        summary = summarize_usage(namespace, get_usage_nodes(namespace, usage_month), usage_month, args.limit)
    except GitLabUsageError as exc:
        print(f"error: {exc}")
        return 2

    if args.json:
        print(json.dumps(summary, separators=(",", ":")))
    else:
        print_human(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
