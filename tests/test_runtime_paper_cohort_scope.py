from pathlib import Path

import pytest

from scripts.runtime_paper_cohort_scope import (
    RuntimePaperCohortScopeError,
    derive_runtime_paper_cohort_db_path,
    record_has_runtime_paper_cohort_lineage,
    validate_runtime_paper_cohort_lineage,
)


@pytest.mark.parametrize(
    ("cohort_id", "cohort_kind", "relative_db_path"),
    [
        ("legacy", "legacy", Path("data/paper_trades.db")),
        (
            "active-20260729",
            "active",
            Path("data/paper_cohorts/active-20260729/paper_trades.db"),
        ),
        (
            "legacy-pending-20260729",
            "legacy_pending",
            Path("data/legacy_pending_paper_cohorts/legacy-pending-20260729/paper_trades.db"),
        ),
    ],
)
def test_validates_lineage_and_derives_the_canonical_db_path(
    tmp_path: Path,
    cohort_id: str,
    cohort_kind: str,
    relative_db_path: Path,
) -> None:
    repo_root = tmp_path / "repo"

    assert validate_runtime_paper_cohort_lineage(cohort_id, cohort_kind) == (
        cohort_id,
        cohort_kind,
    )
    assert (
        derive_runtime_paper_cohort_db_path(
            repo_root,
            cohort_id,
            cohort_kind,
        )
        == repo_root / relative_db_path
    )


@pytest.mark.parametrize(
    ("cohort_id", "cohort_kind"),
    [
        (" legacy", "legacy"),
        ("legacy ", "legacy"),
        ("Legacy", "legacy"),
        ("legacy", " legacy"),
        ("legacy", "legacy "),
        ("", "active"),
        ("-invalid", "active"),
        ("a" * 65, "active"),
        ("legacy", "active"),
        ("active-20260729", "legacy"),
        ("active-20260729", "unknown"),
    ],
)
def test_rejects_padded_invalid_and_incompatible_lineage_pairs(
    cohort_id: str,
    cohort_kind: str,
) -> None:
    with pytest.raises(RuntimePaperCohortScopeError):
        validate_runtime_paper_cohort_lineage(cohort_id, cohort_kind)


def test_record_lineage_requires_an_exact_validated_pair() -> None:
    cohort_id = "legacy-pending-20260729"
    cohort_kind = "legacy_pending"

    assert record_has_runtime_paper_cohort_lineage(
        {
            "runtime_paper_cohort_id": cohort_id,
            "runtime_paper_cohort_kind": cohort_kind,
        },
        cohort_id,
        cohort_kind,
    )
    assert not record_has_runtime_paper_cohort_lineage(
        {
            "runtime_paper_cohort_id": cohort_id,
            "runtime_paper_cohort_kind": "active",
        },
        cohort_id,
        cohort_kind,
    )
    assert not record_has_runtime_paper_cohort_lineage(
        {"runtime_paper_cohort_id": cohort_id},
        cohort_id,
        cohort_kind,
    )
    assert not record_has_runtime_paper_cohort_lineage(
        {"runtime_paper_cohort_id": cohort_id, "runtime_paper_cohort_kind": None},
        cohort_id,
        cohort_kind,
    )


def test_record_lineage_rejects_invalid_requested_lineage() -> None:
    with pytest.raises(RuntimePaperCohortScopeError):
        record_has_runtime_paper_cohort_lineage(
            {},
            "legacy-pending-20260729 ",
            "legacy_pending",
        )
