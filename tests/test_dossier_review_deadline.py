"""Deadline tripwire for the dossier-forgetting functions.

``analysis/dossier_builder.identify_superseded`` and ``clear_on_resolution`` were
built ahead of integration with **test-only** callers and a "review by
2026-06-08; delete with matching tests if still untouched" marker. A date living
only in a code comment rots silently — nothing forces the review when the day
passes. This converts that passive marker into an enforced check: once the
deadline passes the live tripwire fails, forcing an explicit decision (wire the
functions into production OR delete them with their tests), after which this
tripwire is updated or removed.
"""

from datetime import date, datetime, timezone

REVIEW_DEADLINE = date(2026, 6, 8)


def _deadline_passed(today: date, deadline: date = REVIEW_DEADLINE) -> bool:
    return today > deadline


def test_tripwire_logic_fires_strictly_after_deadline():
    assert _deadline_passed(date(2026, 6, 9)) is True
    assert _deadline_passed(date(2026, 6, 8)) is False
    assert _deadline_passed(date(2026, 6, 7)) is False


def test_dossier_forgetting_review_deadline_not_passed():
    today = datetime.now(timezone.utc).date()
    assert not _deadline_passed(today), (
        f"Review deadline {REVIEW_DEADLINE} for the dossier-forgetting functions "
        "(analysis/dossier_builder.identify_superseded / clear_on_resolution — built "
        "ahead of integration, test-only callers) has passed. Decide: integrate them "
        "into production OR delete them with their tests in tests/test_dossier_forgetting.py, "
        "then update or remove this tripwire."
    )
