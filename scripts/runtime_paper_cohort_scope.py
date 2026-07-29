"""Fail-closed runtime paper-cohort lineage helpers for read-only reports."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re


_COHORT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LEGACY_COHORT_ID = "legacy"
_LEGACY_COHORT_KIND = "legacy"
_ACTIVE_COHORT_KIND = "active"
_LEGACY_PENDING_COHORT_KIND = "legacy_pending"
_VALID_COHORT_KINDS = frozenset(
    (
        _LEGACY_COHORT_KIND,
        _ACTIVE_COHORT_KIND,
        _LEGACY_PENDING_COHORT_KIND,
    )
)


class RuntimePaperCohortScopeError(ValueError):
    """Raised when runtime paper-cohort lineage cannot be trusted."""


def validate_runtime_paper_cohort_lineage(
    cohort_id: str,
    cohort_kind: str,
) -> tuple[str, str]:
    """Validate one exact runtime lineage pair without normalization."""

    if type(cohort_id) is not str or not _COHORT_ID_PATTERN.fullmatch(cohort_id):
        raise RuntimePaperCohortScopeError("runtime paper cohort id must be lowercase letters, digits, and hyphens")
    if type(cohort_kind) is not str or cohort_kind not in _VALID_COHORT_KINDS:
        choices = ", ".join(sorted(_VALID_COHORT_KINDS))
        raise RuntimePaperCohortScopeError(f"runtime paper cohort kind must be one of: {choices}")
    if cohort_id == _LEGACY_COHORT_ID and cohort_kind != _LEGACY_COHORT_KIND:
        raise RuntimePaperCohortScopeError("legacy runtime paper cohort id requires legacy cohort kind")
    if cohort_id != _LEGACY_COHORT_ID and cohort_kind == _LEGACY_COHORT_KIND:
        raise RuntimePaperCohortScopeError("nonlegacy runtime paper cohort id requires active or legacy_pending kind")
    return cohort_id, cohort_kind


def derive_runtime_paper_cohort_db_path(
    repo_root: Path,
    cohort_id: str,
    cohort_kind: str,
) -> Path:
    """Return the canonical database path for an exact validated lineage pair."""

    cohort_id, cohort_kind = validate_runtime_paper_cohort_lineage(
        cohort_id,
        cohort_kind,
    )
    data_root = Path(repo_root) / "data"
    if cohort_kind == _LEGACY_COHORT_KIND:
        return data_root / "paper_trades.db"
    if cohort_kind == _ACTIVE_COHORT_KIND:
        return data_root / "paper_cohorts" / cohort_id / "paper_trades.db"
    return data_root / "legacy_pending_paper_cohorts" / cohort_id / "paper_trades.db"


def record_has_runtime_paper_cohort_lineage(
    record: Mapping[str, object],
    cohort_id: str,
    cohort_kind: str,
) -> bool:
    """Return whether a structured record carries exactly the requested lineage."""

    cohort_id, cohort_kind = validate_runtime_paper_cohort_lineage(
        cohort_id,
        cohort_kind,
    )
    if not isinstance(record, Mapping):
        return False
    record_cohort_id = record.get("runtime_paper_cohort_id")
    record_cohort_kind = record.get("runtime_paper_cohort_kind")
    return (
        type(record_cohort_id) is str
        and type(record_cohort_kind) is str
        and record_cohort_id == cohort_id
        and record_cohort_kind == cohort_kind
    )
