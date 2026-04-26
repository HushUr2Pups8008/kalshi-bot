"""GovernanceAdapter protocol + KalshiGovernanceAdapter conformance."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from governance.adapter import GovernanceAdapter, KalshiGovernanceAdapter


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
