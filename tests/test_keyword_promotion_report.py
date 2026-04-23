from scripts.keyword_promotion_report import (
    RECOMMEND_PROMOTE,
    RECOMMEND_REJECT,
    RECOMMEND_WATCH,
    print_report,
    summarize,
)
from tests._helpers import cleanup_tmp_dir, make_tmp_dir, write_jsonl


def _miss(headline: str, ticker: str, source: str) -> dict:
    return {
        "type": "ANALYSIS_REJECTED",
        "reason": "no_keywords",
        "ticker": ticker,
        "source": source,
        "headline": headline,
        "match_score": 0.11,
        "ts": "2026-04-12T12:00:00+00:00",
    }


def test_summarize_groups_promote_watch_and_reject():
    tmp = make_tmp_dir("keyword_promotion_report")
    try:
        path = tmp / "trades.jsonl"
        records = [
            _miss("Deadline passes for Strait of Hormuz blockade", "KX1", "Reuters"),
            _miss("U.S. begins blockade in Strait of Hormuz", "KX2", "AP"),
            _miss("Oil climbs as Strait of Hormuz blockade continues", "KX3", "Reuters"),
            _miss("Tanker traffic reroutes during Strait of Hormuz blockade", "KX4", "BBC"),
            _miss("Analysts warn Strait of Hormuz blockade may widen", "KX5", "AP"),
            _miss("Trump plans to blockade Iran, experts are skeptical", "KX6", "Reuters"),
            _miss("Lawmakers debate blockade Iran strategy", "KX7", "Reuters"),
            _miss("Donald Trump attends rally", "KX8", "Reuters"),
        ]
        write_jsonl(path, records)

        stats = summarize(
            path=path,
            since=None,
            until=None,
            phrases=["strait hormuz", "blockade iran", "hormuz blockade", "donald trump"],
            exclude_test=False,
            max_examples=2,
        )

        grouped = stats["grouped"]
        assert any(row["phrase"] == "strait hormuz" for row in grouped[RECOMMEND_PROMOTE])
        assert any(row["phrase"] == "blockade iran" for row in grouped[RECOMMEND_WATCH])
        assert any(row["phrase"] == "donald trump" for row in grouped[RECOMMEND_REJECT])
    finally:
        cleanup_tmp_dir(tmp)


def test_rejects_zero_hit_phrase():
    tmp = make_tmp_dir("keyword_promotion_report")
    try:
        path = tmp / "trades.jsonl"
        write_jsonl(path, [_miss("NATO summit in Brussels", "KX1", "Reuters")])

        stats = summarize(
            path=path,
            since=None,
            until=None,
            phrases=["strait hormuz"],
            exclude_test=False,
            max_examples=2,
        )

        row = stats["recommendations"][0]
        assert row["recommendation"] == RECOMMEND_REJECT
        assert "zero shadow hits" in row["rationale"]
    finally:
        cleanup_tmp_dir(tmp)


def test_print_report_includes_expected_sections(capsys):
    tmp = make_tmp_dir("keyword_promotion_report")
    try:
        path = tmp / "trades.jsonl"
        write_jsonl(
            path,
            [
                _miss("Deadline passes for Strait of Hormuz blockade", "KX1", "Reuters"),
                _miss("U.S. begins blockade in Strait of Hormuz", "KX2", "AP"),
                _miss("Trump plans to blockade Iran", "KX3", "Reuters"),
            ],
        )

        stats = summarize(
            path=path,
            since=None,
            until=None,
            phrases=["strait hormuz", "blockade iran"],
            exclude_test=False,
            max_examples=1,
        )
        print_report(stats, top=5, max_examples=1)
        output = capsys.readouterr().out

        assert "KEYWORD PROMOTION REPORT" in output
        assert "Promotion Criteria" in output
        assert "Promote" in output
        assert "Watch" in output
        assert "Reject" in output
        assert "Attribution Notes" in output
    finally:
        cleanup_tmp_dir(tmp)
