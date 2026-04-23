from __future__ import annotations

from pathlib import Path

from utils.keyword_diagnostics_helpers import (
    BUCKET_PROMOTE,
    BUCKET_REJECT,
    BUCKET_SHADOW,
    CONCENTRATION_FLAG_THRESHOLD,
    PROMOTE_MIN_HITS,
    PROMOTE_MIN_SCORE,
    evaluate_shadow_phrases,
    load_no_keyword_miss_corpus,
    phrase_matches,
    score_shadow_phrases,
    tokenize_keyword_text,
)
from tests._helpers import write_jsonl


def _miss(
    headline: str,
    *,
    source: str = "Reuters",
    ticker: str = "KXREAL-1",
    ts: str = "2026-04-18T12:00:00+00:00",
) -> dict[str, object]:
    return {
        "type": "ANALYSIS_REJECTED",
        "reason": "no_keywords",
        "headline": headline,
        "source": source,
        "ticker": ticker,
        "ts": ts,
    }


def test_tokenize_keyword_text_removes_stopwords_and_short_tokens():
    tokens = tokenize_keyword_text("Iran War Live Updates: U.S. in the Strait of Hormuz")

    assert tokens == ["iran", "war", "strait", "hormuz"]


def test_phrase_matches_uses_token_subsequence_not_raw_substring():
    assert phrase_matches("strait hormuz", "Strait of Hormuz reopens") is True
    assert phrase_matches("strait hormuz", "Hormuz Strait reopens") is False
    assert phrase_matches("strait hormuz", "Strait remains open") is False


def test_load_no_keyword_miss_corpus_filters_event_type_reason_and_test_records(tmp_path: Path):
    root = tmp_path / "trades"
    write_jsonl(
        root / "archive" / "2026" / "04" / "2026-04-18.jsonl",
        [
            _miss("Strait of Hormuz blockade", ticker="KXREAL-1"),
            _miss("Test record should be excluded", source="r/test", ticker="KXTEST-1"),
            {"type": "ANALYSIS_REJECTED", "reason": "stale_news", "headline": "wrong reason", "source": "Reuters", "ticker": "KXREAL-2", "ts": "2026-04-18T12:01:00+00:00"},
            {"type": "EARLY_STALE_DROP", "reason": "no_keywords", "headline": "wrong type", "source": "Reuters", "ticker": "KXREAL-3", "ts": "2026-04-18T12:02:00+00:00"},
            '{"type": "ANALYSIS_REJECTED"',
        ],
    )

    records, total_lines, malformed = load_no_keyword_miss_corpus(
        root,
        since=None,
        until=None,
        exclude_test=True,
    )

    assert total_lines == 5
    assert malformed == 1
    assert [record["headline"] for record in records] == ["Strait of Hormuz blockade"]


def test_load_no_keyword_miss_corpus_honors_custom_test_record_callback(tmp_path: Path):
    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            _miss("Keep me", source="Reuters", ticker="KXREAL-1"),
            _miss("Drop by custom callback", source="Reuters", ticker="KXREAL-2"),
        ],
    )

    records, _, _ = load_no_keyword_miss_corpus(
        path,
        since=None,
        until=None,
        exclude_test=True,
        is_test_record=lambda record: record.get("ticker") == "KXREAL-2",
    )

    assert [record["ticker"] for record in records] == ["KXREAL-1"]


def test_evaluate_shadow_phrases_tracks_hits_overlap_and_examples():
    records = [
        _miss("UK joins strait of Hormuz blockade", source="Reuters", ticker="KX1"),
        _miss("Strait of Hormuz closes again", source="BBC", ticker="KX2"),
        _miss("NATO summit discusses budgets", source="AP", ticker="KX3"),
        _miss("UK joins strait of Hormuz blockade", source="Reuters", ticker="KX1"),
    ]

    result = evaluate_shadow_phrases(records, ["strait hormuz", "hormuz blockade"], max_examples=2)
    by_phrase = {row["phrase"]: row for row in result["phrases"]}

    assert result["total_miss_events"] == 4
    assert result["events_with_any_hit"] == 3
    assert result["events_with_no_hit"] == 1
    assert result["events_with_overlap"] == 2
    assert result["pair_overlap"]["hormuz blockade + strait hormuz"] == 2

    assert by_phrase["strait hormuz"]["hits"] == 3
    assert by_phrase["strait hormuz"]["sources"] == ["BBC", "Reuters"]
    assert by_phrase["strait hormuz"]["tickers"] == ["KX1", "KX2"]
    assert by_phrase["strait hormuz"]["overlap_hits"] == 2
    assert by_phrase["strait hormuz"]["examples"] == [
        "UK joins strait of Hormuz blockade",
        "Strait of Hormuz closes again",
    ]

    assert by_phrase["hormuz blockade"]["hits"] == 2


def test_evaluate_shadow_phrases_sets_concentration_flag_at_threshold():
    records = [
        _miss("Strait of Hormuz blockade starts", ticker="KX1"),
        _miss("Strait of Hormuz update", ticker="KX1"),
        _miss("Strait of Hormuz reopens", ticker="KX2"),
        _miss("Strait of Hormuz insured traffic resumes", ticker="KX2"),
    ]

    result = evaluate_shadow_phrases(records, ["strait hormuz"])
    concentration = result["phrases"][0]["concentration"]

    assert concentration["fraction"] == CONCENTRATION_FLAG_THRESHOLD
    assert concentration["flag"] is True


def test_score_shadow_phrases_promotes_at_exact_hit_and_source_thresholds_without_concentration():
    phrase_results = [
        {
            "phrase": "threshold phrase",
            "hits": PROMOTE_MIN_HITS,
            "sources": ["Reuters", "BBC"],
            "tickers": ["KX1", "KX2"],
            "ticker_counts": {"KX1": 3, "KX2": 2},
            "examples": ["a"],
            "concentration": {"flag": False},
            "overlap_hits": 0,
        }
    ]

    scored = score_shadow_phrases(phrase_results)

    assert scored[0]["score"] > PROMOTE_MIN_SCORE
    assert scored[0]["bucket"] == BUCKET_PROMOTE
    assert "hits=5" in scored[0]["reason"]
    assert "sources=2" in scored[0]["reason"]


def test_score_shadow_phrases_rejects_zero_hit_phrase():
    phrase_results = [
        {
            "phrase": "never hit",
            "hits": 0,
            "sources": [],
            "tickers": [],
            "ticker_counts": {},
            "examples": [],
            "concentration": {},
            "overlap_hits": 0,
        }
    ]

    scored = score_shadow_phrases(phrase_results)

    assert scored[0]["bucket"] == BUCKET_REJECT
    assert scored[0]["reason"] == "zero hits in miss corpus"


def test_score_shadow_phrases_keeps_shadow_bucket_when_concentration_flag_blocks_promotion():
    phrase_results = [
        {
            "phrase": "concentrated phrase",
            "hits": 6,
            "sources": ["Reuters", "BBC"],
            "tickers": ["KX1"],
            "ticker_counts": {"KX1": 6},
            "examples": ["a"],
            "concentration": {"flag": True},
            "overlap_hits": 0,
        }
    ]

    scored = score_shadow_phrases(phrase_results)

    assert scored[0]["bucket"] == BUCKET_SHADOW
    assert "concentration flag set" in scored[0]["reason"]
    assert scored[0]["score"] == (6 * 4 + 2 * 6 + 1 * 3 - 10)
