"""
Tests for reporting script default trade-log paths (v0.29.5).

Verifies that each script's DEFAULT_LOG_PATH points to the correct source
after the trade-log restructure moved active data to logs/trades/live/.

Rules:
  - Current-state scripts  -> live file   (logs/trades/live/trades.jsonl)
  - Cross-window audit     -> root dir    (logs/trades)
  - Anything with --path   -> can override the default; tests only cover defaults
"""

import scripts.daily_review as daily_review
import scripts.decision_funnel_summary as decision_funnel_summary
import scripts.freshness_diagnostics as freshness_diagnostics
import scripts.match_quality_diagnostics as match_quality_diagnostics
import scripts.pipeline_impact_audit as pipeline_impact_audit
import scripts.signal_edge_diagnostics as signal_edge_diagnostics

_LIVE_FILE_SUFFIX = "trades/live/trades.jsonl"
_TRADES_ROOT_SUFFIX = "logs/trades"


def _ends_with(path_obj, suffix: str) -> bool:
    """Portable suffix check: normalise to forward slashes, case-insensitive."""
    return path_obj.__fspath__().replace("\\", "/").lower().endswith(suffix.lower())


class TestCurrentStateScriptsDefaultToLiveFile:
    """Scripts whose default purpose is current-state reporting must point
    to logs/trades/live/trades.jsonl so they only scan the active log."""

    def test_daily_review_default_is_live_file(self):
        assert _ends_with(daily_review.DEFAULT_TRADES_LOG_PATH, _LIVE_FILE_SUFFIX), (
            f"daily_review default {daily_review.DEFAULT_TRADES_LOG_PATH!r} "
            f"should end with {_LIVE_FILE_SUFFIX!r}"
        )

    def test_freshness_diagnostics_default_is_live_file(self):
        assert _ends_with(freshness_diagnostics.DEFAULT_LOG_PATH, _LIVE_FILE_SUFFIX)

    def test_match_quality_diagnostics_default_is_live_file(self):
        assert _ends_with(match_quality_diagnostics.DEFAULT_LOG_PATH, _LIVE_FILE_SUFFIX)

    def test_decision_funnel_summary_default_is_live_file(self):
        assert _ends_with(decision_funnel_summary.DEFAULT_LOG_PATH, _LIVE_FILE_SUFFIX)

    def test_signal_edge_diagnostics_default_is_live_file(self):
        assert _ends_with(signal_edge_diagnostics.DEFAULT_LOG_PATH, _LIVE_FILE_SUFFIX)


class TestArchiveAwareScriptsDefaultToRoot:
    """Scripts that compare across day boundaries must keep the root directory
    default so iter_trade_records can reach archived partitions."""

    def test_pipeline_impact_audit_default_is_trades_root(self):
        path_str = pipeline_impact_audit.DEFAULT_LOG_PATH.__fspath__().replace("\\", "/")
        assert path_str.endswith("logs/trades"), (
            f"pipeline_impact_audit default {pipeline_impact_audit.DEFAULT_LOG_PATH!r} "
            f"must be the trades root directory (not live-only) so previous-window "
            f"comparisons can access archive partitions"
        )
        assert "live" not in path_str.split("/")[-2:], (
            "pipeline_impact_audit must NOT point into the live/ subdirectory"
        )


class TestDailyReviewHasPathArg:
    """daily_review.py previously had no --path argument; adding it lets users
    override to logs/trades for historical analysis without changing the default."""

    def test_daily_review_parse_args_accepts_path(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["daily_review", "--path", "logs/trades/live/trades.jsonl"],
        )
        args = daily_review.parse_args()
        assert args.path == "logs/trades/live/trades.jsonl"

    def test_daily_review_parse_args_path_default_is_live_file(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["daily_review"])
        args = daily_review.parse_args()
        assert _ends_with(
            type("_P", (), {"__fspath__": lambda _: args.path})(),
            _LIVE_FILE_SUFFIX,
        ), f"--path default {args.path!r} should end with {_LIVE_FILE_SUFFIX!r}"
