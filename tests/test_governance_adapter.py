"""GovernanceAdapter protocol + KalshiGovernanceAdapter conformance."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from governance.adapter import GovernanceAdapter, KalshiGovernanceAdapter


def _write_trade_log(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _ts(seconds_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def test_protocol_declares_required_methods():
    """The Protocol must expose every method the agent loop calls.
    If a method is added to the agent but not to this list, this test
    fails — preventing accidental drift."""
    required = {
        "collect_audit_data",
        "get_active_market_titles",
        "get_recent_headline_samples",
        "get_active_source_count",
        "get_active_source_list",
    }
    declared = {
        name
        for name in dir(GovernanceAdapter)
        if not name.startswith("_")
    }
    missing = required - declared
    assert not missing, f"Protocol missing required methods: {missing}"


def test_kalshi_adapter_conforms_to_protocol(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")
    paper_db = tmp_path / "paper.db"
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log,
        paper_db_path=paper_db,
        market_provider=None,
    )
    assert isinstance(adapter, GovernanceAdapter)


def test_collect_audit_data_returns_four_named_aggregations(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    _write_trade_log(trade_log, [
        {"ts": _ts(60), "type": "EARLY_FRESH_PASS", "source": "Reuters",
         "ticker": "KXTRUMPIRAN-1", "headline": "Iran talks update"},
        {"ts": _ts(60), "type": "MATCH_DIAGNOSTIC", "source": "Reuters",
         "ticker": "KXTRUMPIRAN-1", "headline": "Iran talks update",
         "match_score": 0.6, "would_fail_pre_llm_gate": False,
         "pre_llm_quality_pass": True, "heuristic_flags": []},
        {"ts": _ts(60), "type": "SIGNAL_ANALYSIS_DETAIL", "source": "Reuters",
         "ticker": "KXTRUMPIRAN-1", "headline": "Iran talks update",
         "method": "llm", "llm_result_used": True,
         "estimated_probability": 0.5, "market_price": 0.5},
    ])
    paper_db = tmp_path / "paper.db"
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=paper_db, market_provider=None,
    )
    data = adapter.collect_audit_data(window=timedelta(hours=24))
    assert set(data.keys()) >= {"alignment", "keywords", "reddit", "freshness"}


def test_get_recent_headline_samples_returns_up_to_k_strings(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    records = []
    for i in range(8):
        records.append({
            "ts": _ts(3600 - i),
            "type": "EARLY_FRESH_PASS",
            "source": "r/Turkey",
            "ticker": "KX1",
            "headline": f"Turkey news item {i}",
        })
    _write_trade_log(trade_log, records)
    paper_db = tmp_path / "paper.db"
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=paper_db, market_provider=None,
    )
    samples = adapter.get_recent_headline_samples("r/Turkey", k=5)
    assert len(samples) == 5
    assert all(isinstance(s, str) and s for s in samples)


def test_get_active_source_count_and_list_in_24h_window(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    _write_trade_log(trade_log, [
        {"ts": _ts(60), "type": "EARLY_FRESH_PASS", "source": "Reuters",
         "ticker": "KX1", "headline": "h1"},
        {"ts": _ts(60), "type": "EARLY_FRESH_PASS", "source": "AP",
         "ticker": "KX1", "headline": "h2"},
        {"ts": _ts(60), "type": "EARLY_FRESH_PASS", "source": "Reuters",
         "ticker": "KX1", "headline": "h3"},
        # Outside 24h window — should not be counted.
        {"ts": _ts(48 * 3600), "type": "EARLY_FRESH_PASS", "source": "BBC",
         "ticker": "KX1", "headline": "old"},
    ])
    paper_db = tmp_path / "paper.db"
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=paper_db, market_provider=None,
    )
    assert adapter.get_active_source_count() == 2  # Reuters + AP, BBC excluded
    assert "Reuters" in adapter.get_active_source_list()
    assert "AP" in adapter.get_active_source_list()
    assert "BBC" not in adapter.get_active_source_list()
    # ranked by ingestion volume desc
    assert adapter.get_active_source_list()[0] == "Reuters"


def test_get_active_market_titles_uses_market_provider_when_present(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")
    paper_db = tmp_path / "paper.db"

    class _StubMarket:
        def __init__(self, title):
            self.title = title

    def provider():
        return [_StubMarket("Will X happen?"), _StubMarket("Will Y happen?")]

    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log,
        paper_db_path=paper_db,
        market_provider=provider,
    )
    titles = adapter.get_active_market_titles()
    assert titles == ["Will X happen?", "Will Y happen?"]


def test_collect_audit_data_normalizes_to_dict_shape_consumed_by_evidence(tmp_path):
    """Contract test: the four top-level keys must be dicts (NOT tuples)
    and must carry the keys evidence.py / prompts.py read.

    Regression guard for the Task 6.5 follow-up — scripts/* helpers
    return dataclass-keyed dicts and tuples; the adapter normalizes
    them into the bot-agnostic shape the agent loop depends on. With
    an empty trade log, every collection is empty but the *shape*
    must be intact so downstream code does not crash on a fresh
    deployment.
    """
    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log,
        paper_db_path=tmp_path / "paper.db",
        market_provider=None,
    )
    data = adapter.collect_audit_data(window=timedelta(hours=24))

    # Every top-level key is a dict, never a tuple.
    for key in ("alignment", "reddit", "keywords", "freshness"):
        assert isinstance(data[key], dict), (
            f"data[{key!r}] must be a dict, got {type(data[key]).__name__}"
        )

    # Each section carries the keys evidence.py / prompts.py reach for.
    assert "pairs" in data["alignment"]
    assert isinstance(data["alignment"]["pairs"], list)
    assert "overall_anchor_rate" in data["alignment"]
    assert "overall_n" in data["alignment"]

    assert "subs" in data["reddit"]
    assert isinstance(data["reddit"]["subs"], list)

    assert "candidate_phrases" in data["keywords"]
    assert isinstance(data["keywords"]["candidate_phrases"], list)
    assert "no_keyword_misses" in data["keywords"]

    assert "sources" in data["freshness"]
    assert isinstance(data["freshness"]["sources"], dict)


def test_collect_audit_data_shape_lets_select_candidates_run_without_error(tmp_path):
    """End-to-end shape contract: the agent's evidence pipeline runs
    cleanly on collect_audit_data output, even with an empty trade log.
    Regression for the Task 20 main() blocker."""
    from governance.evidence import select_candidates_for_cadence

    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log,
        paper_db_path=tmp_path / "paper.db",
        market_provider=None,
    )
    audit_data = adapter.collect_audit_data(window=timedelta(hours=24))

    # Empty log: zero candidates produced, but no AttributeError.
    candidates = select_candidates_for_cadence(audit_data, cadence="fast")
    assert candidates == []
    candidates_deep = select_candidates_for_cadence(audit_data, cadence="deep")
    assert candidates_deep == []
