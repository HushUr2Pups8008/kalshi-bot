from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "gitlab_ci_usage.py"
SPEC = importlib.util.spec_from_file_location("gitlab_ci_usage", SCRIPT_PATH)
gitlab_ci_usage = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(gitlab_ci_usage)


def test_namespace_from_git_remote_handles_ssh_url() -> None:
    remote = "git@gitlab.com:HushUr2Pups8008/kalshi-bot.git"

    assert gitlab_ci_usage.namespace_from_remote(remote) == "HushUr2Pups8008"


def test_namespace_from_git_remote_handles_https_url() -> None:
    remote = "https://gitlab.com/example-group/subgroup/project.git"

    assert gitlab_ci_usage.namespace_from_remote(remote) == "example-group"


def test_summarize_usage_computes_remaining_and_percentages() -> None:
    namespace = {"id": 126271773, "full_path": "HushUr2Pups8008", "kind": "user", "plan": "free"}
    nodes = [{"minutes": 168, "sharedRunnersDuration": 9965}]

    summary = gitlab_ci_usage.summarize_usage(namespace, nodes, "2026-05-01")

    assert summary["limit_minutes"] == 400
    assert summary["used_minutes"] == 168
    assert summary["remaining_minutes"] == 232
    assert summary["used_fraction"] == 0.42
    assert summary["runner_duration_seconds"] == 9965
    assert summary["runner_duration_minutes"] == 166.1
    assert summary["month"] == "2026-05"


def test_namespace_gid_uses_namespace_kind() -> None:
    user_namespace = {"id": 126271773, "kind": "user"}
    group_namespace = {"id": 42, "kind": "group"}

    assert gitlab_ci_usage.namespace_gid(user_namespace) == "gid://gitlab/Namespaces::UserNamespace/126271773"
    assert gitlab_ci_usage.namespace_gid(group_namespace) == "gid://gitlab/Namespaces::GroupNamespace/42"
