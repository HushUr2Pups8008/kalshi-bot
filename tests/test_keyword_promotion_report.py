import json
import shutil
import uuid
from pathlib import Path

from scripts.keyword_promotion_report import (
    RECOMMEND_PROMOTE,
    RECOMMEND_REJECT,
    RECOMMEND_WATCH,
    print_report,
    summarize,
)


def _make_tmp_dir() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_keyword_promotion_report"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def _cleanup_tmp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _write_jsonl(path: Path, records) -> None:
    lines = []
    for record in records:
        if isinstance(record, str):
            lines.append(record)
        else:
            lines.append(json.dumps(record))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


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
    tmp = _make_tmp_dir()
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
        _write_jsonl(path, records)

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
        _cleanup_tmp_dir(tmp)


def test_rejects_zero_hit_phrase():
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(path, [_miss("NATO summit in Brussels", "KX1", "Reuters")])

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
        _cleanup_tmp_dir(tmp)


def test_print_report_includes_expected_sections(capsys):
    tmp = _make_tmp_dir()
    try:
        path = tmp / "trades.jsonl"
        _write_jsonl(
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
        _cleanup_tmp_dir(tmp)
