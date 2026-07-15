from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.edge_replay import oos_registry


def _registration_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "profit-oos-2026-08-a",
        "declared_at_utc": "2026-07-15T05:00:00Z",
        "window": {
            "start_utc": "2026-08-01T00:00:00Z",
            "end_utc": "2026-08-08T00:00:00Z",
        },
        "regime_label": "post_profit_accounting",
        "universe_policy": {
            "type": "market_families",
            "market_families": ["KXGDP", "KXWEATHER"],
        },
        "exclude_contamination": True,
    }
    payload.update(overrides)
    return payload


def _hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _write_registry(path: Path, registration: dict[str, object]) -> None:
    stored = dict(registration)
    stored["registration_hash"] = _hash(registration)
    path.write_text(
        json.dumps({"schema_version": 1, "registrations": [stored]}),
        encoding="utf-8",
    )


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    return completed.stdout.strip()


def _commit_registry(
    repo: Path,
    payload: dict[str, object],
    *,
    author_date: str,
    committer_date: str,
) -> tuple[Path, str]:
    registry_path = repo / "docs" / "governance" / "oos-corpus-registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    _write_registry(registry_path, payload)
    _git(repo, "add", str(registry_path.relative_to(repo)))
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": author_date,
        "GIT_COMMITTER_DATE": committer_date,
    }
    _git(repo, "commit", "-m", "register OOS window", env=commit_env)
    commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", commit)
    return registry_path, commit


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "OOS Test")
    _git(path, "config", "user.email", "oos-test@example.com")
    return path


def test_shipped_registry_has_schema_v1_and_no_registrations() -> None:
    registry = oos_registry.load_oos_registry(oos_registry.DEFAULT_REGISTRY_PATH)

    assert registry == {}


def test_load_valid_registration_and_verify_canonical_hash(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload = _registration_payload()
    _write_registry(path, payload)

    registrations = oos_registry.load_oos_registry(path)
    registration = registrations["profit-oos-2026-08-a"]

    assert registration.registration_hash == _hash(payload)
    assert oos_registry.canonical_registration_hash(payload) == _hash(payload)
    assert registration.window_start_utc == "2026-08-01T00:00:00Z"
    assert registration.market_families == ("KXGDP", "KXWEATHER")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"unexpected": "field"}, "unknown registration keys"),
        ({"declared_at_utc": "2026-08-01T00:00:00Z"}, "declared_at_utc must be before"),
        ({"declared_at_utc": "2026-07-15T05:00:00+00:00"}, "canonical UTC"),
        (
            {"window": {"start_utc": "2026-08-08T00:00:00Z", "end_utc": "2026-08-08T00:00:00Z"}},
            "start_utc must be before",
        ),
        ({"exclude_contamination": False}, "exclude_contamination must be true"),
    ],
)
def test_registry_rejects_invalid_registration_shape(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    path = tmp_path / "registry.json"
    _write_registry(path, _registration_payload(**overrides))

    with pytest.raises(oos_registry.OOSRegistryError, match=message):
        oos_registry.load_oos_registry(path)


def test_registry_rejects_hash_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload = _registration_payload()
    stored = dict(payload, registration_hash="0" * 64)
    path.write_text(
        json.dumps({"schema_version": 1, "registrations": [stored]}),
        encoding="utf-8",
    )

    with pytest.raises(oos_registry.OOSRegistryError, match="registration_hash mismatch"):
        oos_registry.load_oos_registry(path)


def test_materialization_rejected_before_closed_window_end(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload = _registration_payload()
    _write_registry(path, payload)
    registration = oos_registry.get_oos_registration("profit-oos-2026-08-a", path)

    with pytest.raises(oos_registry.OOSMaterializationTooEarlyError):
        oos_registry.assert_materializable(
            registration,
            as_of_utc=datetime(2026, 8, 7, 23, 59, 59, tzinfo=timezone.utc),
        )

    oos_registry.assert_materializable(
        registration,
        as_of_utc=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )


def test_registry_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    payload = _registration_payload()
    stored = dict(payload, registration_hash=_hash(payload))
    path.write_text(
        json.dumps({"schema_version": 1, "registrations": [stored, stored]}),
        encoding="utf-8",
    )

    with pytest.raises(oos_registry.OOSRegistryError, match="duplicate registration id"):
        oos_registry.load_oos_registry(path)


@pytest.mark.parametrize(
    ("market_families", "message"),
    [
        (["KXGDP", "kxgdp"], "canonical uppercase"),
        (["KXGDP", "KXGDP"], "duplicates"),
        (["KX", "KXGDP"], "prefix-overlap"),
    ],
)
def test_registry_rejects_ambiguous_market_family_tokens(
    tmp_path: Path,
    market_families: list[str],
    message: str,
) -> None:
    path = tmp_path / "registry.json"
    payload = _registration_payload(
        universe_policy={
            "type": "market_families",
            "market_families": market_families,
        }
    )
    _write_registry(path, payload)

    with pytest.raises(oos_registry.OOSRegistryError, match=message):
        oos_registry.load_oos_registry(path)


def test_attest_registration_history_accepts_pre_window_trusted_commit(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    registry_path, commit = _commit_registry(
        repo,
        _registration_payload(),
        author_date="2026-07-16T00:00:00Z",
        committer_date="2026-07-16T01:02:03Z",
    )
    registration = oos_registry.get_oos_registration(
        "profit-oos-2026-08-a", registry_path
    )

    attestation = oos_registry.attest_oos_registration_history(
        registration,
        registry_path=registry_path,
        integration_time_resolver=lambda repo, commit: datetime(
            2026, 7, 16, 1, 2, 3, tzinfo=timezone.utc
        ),
    )

    assert attestation.trusted_ref == "origin/main"
    assert attestation.commit == commit
    assert attestation.integrated_at_utc == "2026-07-16T01:02:03Z"
    assert attestation.registry_path == "docs/governance/oos-corpus-registry.json"
    assert attestation.registration_hash == registration.registration_hash


def test_attestation_default_resolver_uses_github_pr_merge_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    registry_path, commit = _commit_registry(
        repo,
        _registration_payload(),
        author_date="2026-07-01T00:00:00Z",
        committer_date="2026-07-01T00:00:00Z",
    )
    _git(repo, "remote", "add", "origin", "https://github.com/example/repo.git")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh = fake_bin / "gh"
    response = json.dumps(
        [
            {
                "merge_commit_sha": commit,
                "merged_at": "2026-07-16T01:02:03Z",
            }
        ]
    )
    gh.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{response}'\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    registration = oos_registry.get_oos_registration(
        "profit-oos-2026-08-a", registry_path
    )

    attestation = oos_registry.attest_oos_registration_history(
        registration,
        registry_path=registry_path,
    )

    assert attestation.commit == commit
    assert attestation.integrated_at_utc == "2026-07-16T01:02:03Z"


def test_attestation_uses_first_topological_appearance_not_backdated_child(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    registry_path, first_commit = _commit_registry(
        repo,
        _registration_payload(),
        author_date="2026-07-16T00:00:00Z",
        committer_date="2026-07-16T00:00:00Z",
    )
    document = json.loads(registry_path.read_text(encoding="utf-8"))
    second = _registration_payload(
        id="profit-oos-2026-08-b",
        universe_policy={
            "type": "market_families",
            "market_families": ["KXCPI", "KXJOBS"],
        },
    )
    document["registrations"].append(
        dict(second, registration_hash=_hash(second))
    )
    registry_path.write_text(json.dumps(document), encoding="utf-8")
    _git(repo, "add", str(registry_path.relative_to(repo)))
    child_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-15T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-07-15T00:00:00Z",
    }
    _git(repo, "commit", "-m", "add another registration", env=child_env)
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    registration = oos_registry.get_oos_registration(
        "profit-oos-2026-08-a", registry_path
    )

    attestation = oos_registry.attest_oos_registration_history(
        registration,
        registry_path=registry_path,
        integration_time_resolver=lambda repo, commit: (
            datetime(2026, 7, 16, tzinfo=timezone.utc)
            if commit == first_commit
            else datetime(2026, 7, 15, tzinfo=timezone.utc)
        ),
    )

    assert attestation.commit == first_commit
    assert attestation.integrated_at_utc == "2026-07-16T00:00:00Z"


def test_attestation_uses_protected_first_parent_integration_time(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    registry_path = repo / "docs" / "governance" / "oos-corpus-registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps({"schema_version": 1, "registrations": []}),
        encoding="utf-8",
    )
    _git(repo, "add", str(registry_path.relative_to(repo)))
    initial_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2025-11-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2025-11-01T00:00:00Z",
    }
    _git(repo, "commit", "-m", "add empty registry", env=initial_env)
    _git(repo, "checkout", "-b", "late-registration")
    payload = _registration_payload(
        declared_at_utc="2025-12-01T00:00:00Z",
        window={
            "start_utc": "2026-01-01T00:00:00Z",
            "end_utc": "2026-01-08T00:00:00Z",
        },
    )
    _write_registry(registry_path, payload)
    _git(repo, "add", str(registry_path.relative_to(repo)))
    topic_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2025-12-02T00:00:00Z",
        "GIT_COMMITTER_DATE": "2025-12-02T00:00:00Z",
    }
    _git(repo, "commit", "-m", "backdated registration", env=topic_env)
    _git(repo, "checkout", "main")
    merge_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-02-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-02-01T00:00:00Z",
    }
    _git(
        repo,
        "merge",
        "--no-ff",
        "late-registration",
        "-m",
        "merge registration after window opened",
        env=merge_env,
    )
    merge_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    registration = oos_registry.get_oos_registration(
        "profit-oos-2026-08-a", registry_path
    )

    def resolve_integration_time(repo_root: Path, commit: str) -> datetime:
        assert commit == merge_commit
        return datetime(2026, 2, 1, tzinfo=timezone.utc)

    with pytest.raises(oos_registry.OOSRegistryError, match="trusted commit.*before"):
        oos_registry.attest_oos_registration_history(
            registration,
            registry_path=registry_path,
            integration_time_resolver=resolve_integration_time,
        )


def test_attestation_uses_trusted_integration_time_not_backdated_declaration(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    registry_path, _ = _commit_registry(
        repo,
        _registration_payload(declared_at_utc="2026-07-01T00:00:00Z"),
        author_date="2026-07-01T00:00:00Z",
        committer_date="2026-08-02T00:00:00Z",
    )
    registration = oos_registry.get_oos_registration(
        "profit-oos-2026-08-a", registry_path
    )

    with pytest.raises(oos_registry.OOSRegistryError, match="trusted commit.*before"):
        oos_registry.attest_oos_registration_history(
            registration,
            registry_path=registry_path,
            integration_time_resolver=lambda repo, commit: datetime(
                2026, 8, 2, tzinfo=timezone.utc
            ),
        )


def test_attest_registration_history_rejects_hash_not_on_trusted_ref(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    registry_path, _ = _commit_registry(
        repo,
        _registration_payload(regime_label="trusted_regime"),
        author_date="2026-07-16T00:00:00Z",
        committer_date="2026-07-16T00:00:00Z",
    )
    _write_registry(registry_path, _registration_payload(regime_label="local_regime"))
    registration = oos_registry.get_oos_registration(
        "profit-oos-2026-08-a", registry_path
    )

    with pytest.raises(oos_registry.OOSRegistryError, match="exact id/hash"):
        oos_registry.attest_oos_registration_history(
            registration,
            registry_path=registry_path,
        )


def test_attest_registration_history_rejects_missing_ref(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    registry_path, _ = _commit_registry(
        repo,
        _registration_payload(),
        author_date="2026-07-16T00:00:00Z",
        committer_date="2026-07-16T00:00:00Z",
    )
    registration = oos_registry.get_oos_registration(
        "profit-oos-2026-08-a", registry_path
    )

    with pytest.raises(oos_registry.OOSRegistryError, match="trusted ref is not readable"):
        oos_registry.attest_oos_registration_history(
            registration,
            registry_path=registry_path,
            trusted_ref="origin/missing",
        )


def test_attest_registration_history_rejects_missing_registry_path(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    registry_path, _ = _commit_registry(
        repo,
        _registration_payload(),
        author_date="2026-07-16T00:00:00Z",
        committer_date="2026-07-16T00:00:00Z",
    )
    registration = oos_registry.get_oos_registration(
        "profit-oos-2026-08-a", registry_path
    )

    with pytest.raises(oos_registry.OOSRegistryError, match="registry path.*trusted ref"):
        oos_registry.attest_oos_registration_history(
            registration,
            registry_path=repo / "docs" / "governance" / "missing.json",
        )
