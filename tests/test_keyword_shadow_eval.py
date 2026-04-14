"""Tests for scripts/keyword_shadow_eval.py.

Tests cover evaluate_phrases() (pure computation) and load_miss_corpus() (I/O).
No live config is touched by any test.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from scripts.keyword_shadow_eval import (
    BUCKET_PROMOTE,
    BUCKET_REJECT,
    BUCKET_SHADOW,
    CONCENTRATION_FLAG_THRESHOLD,
    DEFAULT_SHADOW_PHRASES,
    PROMOTE_MIN_HITS,
    PROMOTE_MIN_SCORE,
    PROMOTE_MIN_SOURCES,
    _phrase_matches,
    evaluate_phrases,
    load_miss_corpus,
    parse_date_end,
    parse_date_start,
    score_phrases,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tmp_dir() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_keyword_shadow_eval"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def _cleanup_tmp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _write_jsonl(path: Path, records: list) -> None:
    lines = []
    for record in records:
        if isinstance(record, str):
            lines.append(record)
        else:
            lines.append(json.dumps(record))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _miss(headline: str, ticker: str = "KXTEST-1", source: str = "Reuters") -> dict:
    return {
        "type": "ANALYSIS_REJECTED",
        "reason": "no_keywords",
        "ticker": ticker,
        "source": source,
        "headline": headline,
        "match_score": 0.10,
        "ts": "2026-04-12T12:00:00+00:00",
    }


def _other(headline: str) -> dict:
    """A non-miss event that should be ignored by load_miss_corpus."""
    return {
        "type": "EARLY_STALE_DROP",
        "source": "Reuters",
        "headline": headline,
        "ts": "2026-04-12T12:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# evaluate_phrases -- core computation tests
# ---------------------------------------------------------------------------


class TestEvaluatePhrases:
    def test_empty_corpus_returns_zero_hits(self):
        result = evaluate_phrases([], ["strait hormuz", "hormuz blockade"])
        assert result["total_miss_events"] == 0
        assert result["events_with_any_hit"] == 0
        assert result["events_with_no_hit"] == 0
        assert result["events_with_overlap"] == 0
        for pr in result["phrases"]:
            assert pr["hits"] == 0

    def test_phrase_hit_in_matching_headline(self):
        records = [_miss("Iran War Live Updates: U.S. Begins Blockade in Strait of Hormuz")]
        result = evaluate_phrases(records, ["strait hormuz"])
        pr = result["phrases"][0]
        assert pr["phrase"] == "strait hormuz"
        assert pr["hits"] == 1
        assert result["events_with_any_hit"] == 1
        assert result["events_with_no_hit"] == 0

    def test_phrase_miss_in_irrelevant_headline(self):
        records = [_miss("NATO summit discusses budget contributions")]
        result = evaluate_phrases(records, ["strait hormuz"])
        pr = result["phrases"][0]
        assert pr["hits"] == 0
        assert result["events_with_any_hit"] == 0
        assert result["events_with_no_hit"] == 1

    def test_match_is_case_insensitive(self):
        records = [_miss("STRAIT OF HORMUZ BLOCKED BY US NAVY")]
        result = evaluate_phrases(records, ["strait hormuz"])
        assert result["phrases"][0]["hits"] == 1

    def test_phrase_not_matched_on_partial_word(self):
        # "hormuz" is present but "strait" is not -- "strait hormuz" should not match
        records = [_miss("US Navy approaches Hormuz region")]
        result = evaluate_phrases(records, ["strait hormuz"])
        assert result["phrases"][0]["hits"] == 0

    def test_phrase_not_matched_when_tokens_reversed(self):
        # "Hormuz Strait" tokenises to ["hormuz", "strait"] -- wrong order for "strait hormuz"
        records = [_miss("Hormuz Strait Closed to Tankers")]
        result = evaluate_phrases(records, ["strait hormuz"])
        assert result["phrases"][0]["hits"] == 0

    def test_tokenizer_removes_stopword_between_tokens(self):
        # "Strait of Hormuz" -> tokens ["strait", "hormuz"] (of = stopword) -> matches
        assert _phrase_matches("strait hormuz", "Strait of Hormuz") is True

    def test_phrase_matches_direct_adjacency(self):
        # "hormuz blockade" -- no stopword between tokens
        assert _phrase_matches("hormuz blockade", "US Plans Hormuz Blockade") is True

    def test_phrase_no_match_missing_second_token(self):
        assert _phrase_matches("strait hormuz", "strait is clear") is False

    def test_multiple_phrases_both_hit(self):
        headline = "UK will not join strait of Hormuz blockade, sources say"
        records = [_miss(headline)]
        result = evaluate_phrases(records, ["strait hormuz", "hormuz blockade"])
        assert result["phrases"][0]["hits"] == 1  # strait hormuz
        assert result["phrases"][1]["hits"] == 1  # hormuz blockade

    def test_overlap_counted_when_two_phrases_match_same_event(self):
        # Tokens: ["join", "strait", "hormuz", "blockade", "sources"]
        # "strait hormuz" matches at [1,2]; "hormuz blockade" matches at [2,3]
        headline = "UK will not join strait of Hormuz blockade, sources say"
        records = [_miss(headline)]
        result = evaluate_phrases(records, ["strait hormuz", "hormuz blockade"])
        assert result["events_with_overlap"] == 1
        # pair key is sorted alphabetically: "hormuz blockade" < "strait hormuz"
        assert "hormuz blockade + strait hormuz" in result["pair_overlap"]
        assert result["pair_overlap"]["hormuz blockade + strait hormuz"] == 1

    def test_no_overlap_when_phrases_hit_different_events(self):
        records = [
            _miss("Iran War: U.S. Begins Blockade in Strait of Hormuz"),
            _miss("Trump Plans to Blockade Iran, Experts Are Skeptical"),
        ]
        result = evaluate_phrases(records, ["strait hormuz", "blockade iran"])
        assert result["events_with_overlap"] == 0
        assert result["events_with_any_hit"] == 2

    def test_source_and_ticker_collected_per_phrase(self):
        records = [
            _miss("Iran War: U.S. Begins Blockade in Strait of Hormuz", ticker="KXIRAN-1", source="Reuters"),
            _miss("Deadline Passes for Strait of Hormuz Blockade", ticker="KXIRAN-2", source="BBC"),
        ]
        result = evaluate_phrases(records, ["strait hormuz"])
        pr = result["phrases"][0]
        assert pr["hits"] == 2
        assert "Reuters" in pr["sources"]
        assert "BBC" in pr["sources"]
        assert "KXIRAN-1" in pr["tickers"]
        assert "KXIRAN-2" in pr["tickers"]

    def test_concentration_flag_triggered_above_threshold(self):
        # 3 hits, 2 on same ticker -> fraction = 2/3 = 0.667 >= 0.50 -> flag
        # All three headlines contain "strait hormuz" as adjacent tokens (after stopword removal)
        records = [
            _miss("Strait of Hormuz Blockade Starts", ticker="KXIRAN-1"),
            _miss("Strait of Hormuz Deadline Passes", ticker="KXIRAN-1"),
            _miss("Update: U.S. Strait of Hormuz Vessels Positioned", ticker="KXIRAN-2"),
        ]
        result = evaluate_phrases(records, ["strait hormuz"])
        conc = result["phrases"][0]["concentration"]
        assert conc["flag"] is True
        assert conc["top_ticker"] == "KXIRAN-1"
        assert conc["top_ticker_hits"] == 2
        assert conc["fraction"] > CONCENTRATION_FLAG_THRESHOLD

    def test_concentration_flag_not_triggered_when_distributed(self):
        # 3 hits, each on different ticker -> fraction = 1/3 < 0.50 -> no flag
        records = [
            _miss("Strait of Hormuz Blockade", ticker="KXIRAN-1"),
            _miss("Strait of Hormuz Update", ticker="KXIRAN-2"),
            _miss("Strait of Hormuz Report", ticker="KXIRAN-3"),
        ]
        result = evaluate_phrases(records, ["strait hormuz"])
        conc = result["phrases"][0]["concentration"]
        assert conc["flag"] is False

    def test_examples_capped_at_max_examples(self):
        records = [
            _miss(f"Iran War Live Updates: Strait of Hormuz Blockade Day {i}")
            for i in range(10)
        ]
        result = evaluate_phrases(records, ["strait hormuz"], max_examples=3)
        assert len(result["phrases"][0]["examples"]) <= 3

    def test_duplicate_headlines_not_repeated_in_examples(self):
        headline = "Iran War Live Updates: U.S. Begins Blockade in Strait of Hormuz"
        records = [_miss(headline) for _ in range(5)]
        result = evaluate_phrases(records, ["strait hormuz"])
        assert result["phrases"][0]["examples"].count(headline) == 1

    def test_no_hit_event_in_no_hit_count(self):
        records = [
            _miss("Iran War Live Updates: Strait of Hormuz Blockade"),  # hit
            _miss("NATO summit held in Brussels"),  # no hit
            _miss("UN Security Council meets on Taiwan"),  # no hit
        ]
        result = evaluate_phrases(records, ["strait hormuz"])
        assert result["events_with_any_hit"] == 1
        assert result["events_with_no_hit"] == 2

    def test_default_shadow_phrases_are_non_empty(self):
        assert len(DEFAULT_SHADOW_PHRASES) >= 1
        for phrase in DEFAULT_SHADOW_PHRASES:
            assert isinstance(phrase, str)
            assert phrase.strip() == phrase


# ---------------------------------------------------------------------------
# load_miss_corpus -- I/O tests
# ---------------------------------------------------------------------------


class TestLoadMissCorpus:
    def test_returns_empty_when_file_missing(self):
        records, total, malformed = load_miss_corpus(
            Path("/nonexistent/trades.jsonl"), None, None, False
        )
        assert records == []
        assert total == 0
        assert malformed == 0

    def test_loads_no_keywords_events_only(self):
        tmp = _make_tmp_dir()
        try:
            path = tmp / "trades.jsonl"
            _write_jsonl(
                path,
                [
                    _miss("Iran War Live Updates: Strait of Hormuz Blockade"),
                    _miss("Another keyword miss"),
                    _other("Some other event that should be ignored"),
                    {
                        "type": "ANALYSIS_REJECTED",
                        "reason": "stale_news",
                        "ticker": "KXIRAN-1",
                        "source": "BBC",
                        "headline": "Old stale headline",
                        "ts": "2026-04-12T12:00:00+00:00",
                    },
                ],
            )
            records, total, malformed = load_miss_corpus(path, None, None, False)
            assert len(records) == 2
            assert total == 4
            assert malformed == 0
            assert all(r["reason"] == "no_keywords" for r in records)
        finally:
            _cleanup_tmp_dir(tmp)

    def test_date_window_filters_records(self):
        tmp = _make_tmp_dir()
        try:
            path = tmp / "trades.jsonl"
            _write_jsonl(
                path,
                [
                    {**_miss("Early headline"), "ts": "2026-04-10T12:00:00+00:00"},
                    {**_miss("In-window headline"), "ts": "2026-04-12T12:00:00+00:00"},
                    {**_miss("Late headline"), "ts": "2026-04-14T12:00:00+00:00"},
                ],
            )
            since = parse_date_start("2026-04-11")
            until = parse_date_end("2026-04-13")
            records, total, malformed = load_miss_corpus(path, since, until, False)
            assert len(records) == 1
            assert records[0]["headline"] == "In-window headline"
        finally:
            _cleanup_tmp_dir(tmp)

    def test_exclude_test_filters_kxtest_ticker(self):
        tmp = _make_tmp_dir()
        try:
            path = tmp / "trades.jsonl"
            _write_jsonl(
                path,
                [
                    _miss("Real headline", ticker="KXIRAN-1"),
                    _miss("Test headline", ticker="KXTEST-1"),
                ],
            )
            records, _, _ = load_miss_corpus(path, None, None, exclude_test=True)
            assert len(records) == 1
            assert records[0]["headline"] == "Real headline"
        finally:
            _cleanup_tmp_dir(tmp)

    def test_malformed_lines_counted_not_crashed(self):
        tmp = _make_tmp_dir()
        try:
            path = tmp / "trades.jsonl"
            # Mix of valid and invalid JSON
            path.write_text(
                json.dumps(_miss("Valid miss")) + "\n"
                + "NOT VALID JSON {\n"
                + json.dumps(_miss("Another valid miss")) + "\n",
                encoding="utf-8",
            )
            records, total, malformed = load_miss_corpus(path, None, None, False)
            assert malformed == 1
            assert len(records) == 2
            assert total == 3
        finally:
            _cleanup_tmp_dir(tmp)


# ---------------------------------------------------------------------------
# score_phrases -- promotion-readiness scoring tests
# ---------------------------------------------------------------------------


def _phrase_result(
    phrase: str = "strait hormuz",
    hits: int = 5,
    sources: list[str] | None = None,
    tickers: list[str] | None = None,
    overlap_hits: int = 0,
    concentration_flag: bool = False,
) -> dict:
    """Build a minimal phrase result dict as returned by evaluate_phrases."""
    if sources is None:
        sources = ["Reuters", "BBC"]
    if tickers is None:
        tickers = ["KXIRAN-1", "KXIRAN-2", "KXIRAN-3"]
    conc: dict = {}
    if hits > 0 and tickers:
        top = tickers[0]
        top_count = 2 if concentration_flag else 1
        conc = {
            "top_ticker": top,
            "top_ticker_hits": top_count,
            "fraction": round(top_count / hits, 3),
            "flag": concentration_flag,
        }
    return {
        "phrase": phrase,
        "hits": hits,
        "sources": sources,
        "tickers": tickers,
        "ticker_counts": {t: 1 for t in tickers},
        "examples": [],
        "concentration": conc,
        "overlap_hits": overlap_hits,
    }


class TestScorePhrases:
    def test_score_fields_added_to_result(self):
        results = [_phrase_result(hits=5, sources=["Reuters", "BBC"], tickers=["KXIRAN-1", "KXIRAN-2"])]
        score_phrases(results)
        assert "score" in results[0]
        assert "bucket" in results[0]
        assert "reason" in results[0]

    def test_score_formula_no_penalties(self):
        # score = hits*4 + sources*6 + tickers*3 (no concentration, no overlap)
        pr = _phrase_result(hits=5, sources=["Reuters", "BBC"], tickers=["KXIRAN-1", "KXIRAN-2"], overlap_hits=0)
        score_phrases([pr])
        expected = 5 * 4 + 2 * 6 + 2 * 3
        assert pr["score"] == expected

    def test_concentration_penalty_applied(self):
        pr_clean = _phrase_result(hits=5, sources=["Reuters", "BBC"], tickers=["KXIRAN-1", "KXIRAN-2"],
                                  concentration_flag=False)
        pr_conc = _phrase_result(hits=5, sources=["Reuters", "BBC"], tickers=["KXIRAN-1", "KXIRAN-2"],
                                 concentration_flag=True)
        score_phrases([pr_clean, pr_conc])
        assert pr_clean["score"] == pr_conc["score"] + 10

    def test_overlap_penalty_applied(self):
        pr_no_overlap = _phrase_result(hits=5, sources=["Reuters", "BBC"], tickers=["KXIRAN-1", "KXIRAN-2"],
                                       overlap_hits=0)
        pr_overlap = _phrase_result(hits=5, sources=["Reuters", "BBC"], tickers=["KXIRAN-1", "KXIRAN-2"],
                                    overlap_hits=3)
        score_phrases([pr_no_overlap, pr_overlap])
        assert pr_no_overlap["score"] == pr_overlap["score"] + 3 * 2

    def test_bucket_promote_candidate_when_all_thresholds_met(self):
        # hits >= PROMOTE_MIN_HITS, sources >= PROMOTE_MIN_SOURCES, score >= PROMOTE_MIN_SCORE,
        # no concentration flag
        pr = _phrase_result(hits=PROMOTE_MIN_HITS, sources=["Reuters", "BBC"],
                            tickers=["KXIRAN-1", "KXIRAN-2", "KXIRAN-3"],
                            concentration_flag=False, overlap_hits=0)
        score_phrases([pr])
        assert pr["score"] >= PROMOTE_MIN_SCORE
        assert pr["bucket"] == BUCKET_PROMOTE

    def test_bucket_reject_when_hits_zero(self):
        pr = _phrase_result(hits=0, sources=[], tickers=[])
        score_phrases([pr])
        assert pr["bucket"] == BUCKET_REJECT
        assert "zero hits" in pr["reason"]

    def test_bucket_shadow_when_hits_below_min(self):
        pr = _phrase_result(hits=PROMOTE_MIN_HITS - 1, sources=["Reuters", "BBC"],
                            tickers=["KXIRAN-1", "KXIRAN-2", "KXIRAN-3"], overlap_hits=0)
        score_phrases([pr])
        assert pr["bucket"] == BUCKET_SHADOW
        assert f"hits {pr['hits']} < {PROMOTE_MIN_HITS}" in pr["reason"]

    def test_bucket_shadow_when_sources_below_min(self):
        pr = _phrase_result(hits=10, sources=["Reuters"],
                            tickers=["KXIRAN-1", "KXIRAN-2", "KXIRAN-3"], overlap_hits=0)
        score_phrases([pr])
        assert pr["bucket"] == BUCKET_SHADOW
        assert f"sources 1 < {PROMOTE_MIN_SOURCES}" in pr["reason"]

    def test_bucket_shadow_when_concentration_flagged(self):
        pr = _phrase_result(hits=10, sources=["Reuters", "BBC"],
                            tickers=["KXIRAN-1", "KXIRAN-2", "KXIRAN-3"],
                            concentration_flag=True, overlap_hits=0)
        score_phrases([pr])
        assert pr["bucket"] == BUCKET_SHADOW
        assert "concentration flag" in pr["reason"]

    def test_evaluate_phrases_includes_overlap_hits_field(self):
        # Two phrases that both match the same event -- each should have overlap_hits=1
        headline = "UK will not join strait of Hormuz blockade, sources say"
        records = [_miss(headline)]
        result = evaluate_phrases(records, ["strait hormuz", "hormuz blockade"])
        for pr in result["phrases"]:
            assert "overlap_hits" in pr
            assert pr["overlap_hits"] == 1

    def test_overlap_hits_zero_when_phrases_hit_different_events(self):
        records = [
            _miss("Iran War: U.S. Begins Blockade in Strait of Hormuz"),
            _miss("Trump Plans to Blockade Iran, Experts Are Skeptical"),
        ]
        result = evaluate_phrases(records, ["strait hormuz", "blockade iran"])
        for pr in result["phrases"]:
            assert pr["overlap_hits"] == 0
