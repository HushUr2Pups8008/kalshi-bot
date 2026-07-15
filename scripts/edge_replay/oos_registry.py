"""Fail-closed registry for prospectively declared OOS corpus windows."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 1
DEFAULT_REGISTRY_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "governance"
    / "oos-corpus-registry.json"
)

_ROOT_KEYS: Final = frozenset({"schema_version", "registrations"})
_REGISTRATION_KEYS: Final = frozenset(
    {
        "id",
        "declared_at_utc",
        "window",
        "regime_label",
        "universe_policy",
        "exclude_contamination",
        "registration_hash",
    }
)
_WINDOW_KEYS: Final = frozenset({"start_utc", "end_utc"})
_UNIVERSE_POLICY_KEYS: Final = frozenset({"type", "market_families"})
_ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE: Final = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_MARKET_FAMILY_RE: Final = re.compile(r"^[A-Z0-9][A-Z0-9_:-]{1,127}$")
_TRUSTED_REF_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


class OOSRegistryError(ValueError):
    """Registry schema or integrity failure."""


class OOSRegistrationNotFoundError(OOSRegistryError):
    """Requested registration id is absent."""


class OOSMaterializationTooEarlyError(OOSRegistryError):
    """Registered closed window has not ended yet."""


@dataclass(frozen=True)
class OOSRegistration:
    id: str
    declared_at_utc: str
    window_start_utc: str
    window_end_utc: str
    regime_label: str
    market_families: tuple[str, ...]
    exclude_contamination: bool
    registration_hash: str


@dataclass(frozen=True)
class OOSRegistrationAttestation:
    """Protected-history evidence for a prospective OOS registration."""

    registration_id: str
    registration_hash: str
    trusted_ref: str
    commit: str
    committed_at_utc: str
    registry_path: str


def _parse_canonical_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise OOSRegistryError(f"{field_name} must be a canonical UTC string")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise OOSRegistryError(
            f"{field_name} must use canonical UTC YYYY-MM-DDTHH:MM:SSZ"
        ) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise OOSRegistryError(
            f"{field_name} must use canonical UTC YYYY-MM-DDTHH:MM:SSZ"
        )
    return parsed


def canonical_registration_hash(registration: Mapping[str, Any]) -> str:
    """Hash canonical JSON, excluding only the self-referential hash field."""
    payload = {
        key: value
        for key, value in registration.items()
        if key != "registration_hash"
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    keys = set(value)
    unknown = sorted(keys - expected)
    missing = sorted(expected - keys)
    if unknown:
        raise OOSRegistryError(f"unknown {label} keys: {unknown}")
    if missing:
        raise OOSRegistryError(f"missing {label} keys: {missing}")


def _validate_registration(raw: object) -> OOSRegistration:
    if not isinstance(raw, dict):
        raise OOSRegistryError("each registration must be an object")
    _exact_keys(raw, _REGISTRATION_KEYS, label="registration")

    registration_id = raw["id"]
    if not isinstance(registration_id, str) or not _ID_RE.fullmatch(
        registration_id
    ):
        raise OOSRegistryError("registration id has invalid format")

    declared_at = _parse_canonical_utc(
        raw["declared_at_utc"], field_name="declared_at_utc"
    )
    window = raw["window"]
    if not isinstance(window, dict):
        raise OOSRegistryError("window must be an object")
    _exact_keys(window, _WINDOW_KEYS, label="window")
    start = _parse_canonical_utc(window["start_utc"], field_name="window.start_utc")
    end = _parse_canonical_utc(window["end_utc"], field_name="window.end_utc")
    if declared_at >= start:
        raise OOSRegistryError("declared_at_utc must be before window.start_utc")
    if start >= end:
        raise OOSRegistryError("window.start_utc must be before window.end_utc")

    regime_label = raw["regime_label"]
    if not isinstance(regime_label, str) or not regime_label.strip():
        raise OOSRegistryError("regime_label must be a non-empty string")
    if regime_label != regime_label.strip():
        raise OOSRegistryError("regime_label must not have surrounding whitespace")

    policy = raw["universe_policy"]
    if not isinstance(policy, dict):
        raise OOSRegistryError("universe_policy must be an object")
    _exact_keys(policy, _UNIVERSE_POLICY_KEYS, label="universe_policy")
    if policy["type"] != "market_families":
        raise OOSRegistryError("universe_policy.type must be 'market_families'")
    families = policy["market_families"]
    if not isinstance(families, list) or not families:
        raise OOSRegistryError(
            "universe_policy.market_families must be a non-empty list"
        )
    if any(
        not isinstance(family, str) or not _MARKET_FAMILY_RE.fullmatch(family)
        for family in families
    ):
        raise OOSRegistryError(
            "market_families must contain canonical uppercase family tokens"
        )
    if len(set(families)) != len(families):
        raise OOSRegistryError("market_families must not contain duplicates")
    for index, family in enumerate(families):
        for other in families[index + 1 :]:
            if family.startswith(other) or other.startswith(family):
                raise OOSRegistryError(
                    "market_families must not contain prefix-overlapping tokens"
                )

    if raw["exclude_contamination"] is not True:
        raise OOSRegistryError("exclude_contamination must be true")

    registration_hash = raw["registration_hash"]
    if not isinstance(registration_hash, str) or not _HASH_RE.fullmatch(
        registration_hash
    ):
        raise OOSRegistryError("registration_hash must be 64 lowercase hex characters")
    expected_hash = canonical_registration_hash(raw)
    if registration_hash != expected_hash:
        raise OOSRegistryError(
            f"registration_hash mismatch for {registration_id!r}: "
            f"expected {expected_hash}, got {registration_hash}"
        )

    return OOSRegistration(
        id=registration_id,
        declared_at_utc=raw["declared_at_utc"],
        window_start_utc=window["start_utc"],
        window_end_utc=window["end_utc"],
        regime_label=regime_label,
        market_families=tuple(families),
        exclude_contamination=True,
        registration_hash=registration_hash,
    )


def _parse_registry_document(raw: object) -> dict[str, OOSRegistration]:
    if not isinstance(raw, dict):
        raise OOSRegistryError("OOS registry root must be an object")
    _exact_keys(raw, _ROOT_KEYS, label="registry root")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
        raise OOSRegistryError(
            f"schema_version must be integer {SCHEMA_VERSION}"
        )
    entries = raw["registrations"]
    if not isinstance(entries, list):
        raise OOSRegistryError("registrations must be a list")

    registrations: dict[str, OOSRegistration] = {}
    for entry in entries:
        registration = _validate_registration(entry)
        if registration.id in registrations:
            raise OOSRegistryError(
                f"duplicate registration id: {registration.id!r}"
            )
        registrations[registration.id] = registration
    return registrations


def load_oos_registry(
    path: Path | str = DEFAULT_REGISTRY_PATH,
) -> dict[str, OOSRegistration]:
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OOSRegistryError(f"OOS registry not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise OOSRegistryError(f"OOS registry is not valid JSON: {target}") from exc
    return _parse_registry_document(raw)


def get_oos_registration(
    registration_id: str,
    path: Path | str = DEFAULT_REGISTRY_PATH,
) -> OOSRegistration:
    registrations = load_oos_registry(path)
    try:
        return registrations[registration_id]
    except KeyError as exc:
        raise OOSRegistrationNotFoundError(
            f"OOS registration not found: {registration_id!r}"
        ) from exc


def _run_git(repo_root: Path, *args: str, error: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OOSRegistryError(error) from exc
    if completed.returncode != 0:
        raise OOSRegistryError(error)
    return completed.stdout.strip()


def _git_object_exists(repo_root: Path, object_name: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-e", object_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OOSRegistryError("trusted commit is not readable") from exc
    return completed.returncode == 0


def _parse_git_committer_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OOSRegistryError("trusted commit timestamp is not readable") from exc
    if parsed.tzinfo is None:
        raise OOSRegistryError("trusted commit timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def attest_oos_registration_history(
    registration: OOSRegistration,
    *,
    registry_path: Path | str = DEFAULT_REGISTRY_PATH,
    trusted_ref: str = "origin/main",
    repo_root: Path | str | None = None,
) -> OOSRegistrationAttestation:
    """Prove an exact registration existed on trusted history before its window.

    The trusted commit's committer timestamp is used as the practical protected-
    history declaration time. The self-declared ``declared_at_utc`` field cannot
    satisfy this attestation by itself.
    """
    if (
        not _TRUSTED_REF_RE.fullmatch(trusted_ref)
        or ".." in trusted_ref
        or "@{" in trusted_ref
        or trusted_ref.endswith(("/", ".", ".lock"))
    ):
        raise OOSRegistryError("trusted ref has invalid format")

    target = Path(registry_path).resolve(strict=False)
    probe = Path(repo_root).resolve() if repo_root is not None else target.parent
    top_level = _run_git(
        probe,
        "rev-parse",
        "--show-toplevel",
        error="registry path is not inside a readable git repository",
    )
    resolved_root = Path(top_level).resolve()
    try:
        relative_path = target.relative_to(resolved_root)
    except ValueError as exc:
        raise OOSRegistryError("registry path must be inside the git repository") from exc
    registry_tree_path = relative_path.as_posix()

    trusted_commit = _run_git(
        resolved_root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{trusted_ref}^{{commit}}",
        error=f"trusted ref is not readable: {trusted_ref!r}",
    )
    if not _GIT_OBJECT_RE.fullmatch(trusted_commit):
        raise OOSRegistryError(f"trusted ref is not readable: {trusted_ref!r}")

    history = _run_git(
        resolved_root,
        "rev-list",
        "--reverse",
        "--topo-order",
        trusted_commit,
        "--",
        registry_tree_path,
        error="trusted registry history is not readable",
    ).splitlines()
    if not history:
        raise OOSRegistryError(
            f"registry path is absent from trusted ref: {registry_tree_path}"
        )

    saw_registry_blob = False
    matching_commits: list[tuple[datetime, str]] = []
    for commit in history:
        if not _GIT_OBJECT_RE.fullmatch(commit) or not _git_object_exists(
            resolved_root, f"{commit}^{{commit}}"
        ):
            raise OOSRegistryError("trusted commit is not readable")
        blob_name = f"{commit}:{registry_tree_path}"
        if not _git_object_exists(resolved_root, blob_name):
            continue
        saw_registry_blob = True
        registry_text = _run_git(
            resolved_root,
            "cat-file",
            "blob",
            blob_name,
            error="trusted registry blob is not readable",
        )
        try:
            historical = _parse_registry_document(json.loads(registry_text))
        except (json.JSONDecodeError, OOSRegistryError):
            continue
        historical_registration = historical.get(registration.id)
        if (
            historical_registration is None
            or historical_registration.registration_hash
            != registration.registration_hash
        ):
            continue
        committed_at = _parse_git_committer_utc(
            _run_git(
                resolved_root,
                "show",
                "-s",
                "--format=%cI",
                commit,
                error="trusted commit timestamp is not readable",
            )
        )
        matching_commits.append((committed_at, commit))

    if not saw_registry_blob:
        raise OOSRegistryError(
            f"registry path is absent from trusted ref: {registry_tree_path}"
        )
    if not matching_commits:
        raise OOSRegistryError(
            f"exact id/hash for registration {registration.id!r} "
            f"does not exist on trusted ref {trusted_ref!r}"
        )

    committed_at, commit = matching_commits[0]
    window_start = _parse_canonical_utc(
        registration.window_start_utc,
        field_name="window.start_utc",
    )
    committed_at_utc = committed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    if committed_at >= window_start:
        raise OOSRegistryError(
            f"trusted commit {commit} must be before window.start_utc; "
            f"committed_at={committed_at_utc}, "
            f"window_start={registration.window_start_utc}"
        )
    return OOSRegistrationAttestation(
        registration_id=registration.id,
        registration_hash=registration.registration_hash,
        trusted_ref=trusted_ref,
        commit=commit,
        committed_at_utc=committed_at_utc,
        registry_path=registry_tree_path,
    )


def assert_materializable(
    registration: OOSRegistration,
    *,
    as_of_utc: datetime,
) -> None:
    if as_of_utc.tzinfo is None:
        raise OOSRegistryError("materialization as_of_utc must be timezone-aware")
    end = _parse_canonical_utc(
        registration.window_end_utc,
        field_name="window.end_utc",
    )
    normalized_as_of = as_of_utc.astimezone(timezone.utc)
    if normalized_as_of < end:
        raise OOSMaterializationTooEarlyError(
            f"registration {registration.id!r} cannot materialize before "
            f"closed window end {registration.window_end_utc}; "
            f"as_of={normalized_as_of.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )
