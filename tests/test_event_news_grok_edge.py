"""Paper Grok edge math, spend cap, and the fail-closed queue."""

from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from config import cfg
from main import TradingBot
from utils.event_news_desk_flags import (
    EVENT_NEWS_GROK_EDGE_SCAN,
    EVENT_NEWS_GROK_EXIT_WATCH,
    EVENT_NEWS_GROK_KALSHI_SERIES,
    _DESK_FLAG_NAMES,
    event_news_any_live_desk_flag,
)
from utils.event_news_grok_edge import (
    GROK_DAILY_USD_CAP,
    GROK_EDGE_TIMEOUT_SECONDS,
    GROK_MAX_SEARCH_RESULTS,
    MAX_MARKETS_PER_CYCLE,
    MIN_CALL_RESERVE_USD,
    PAPER_CLOSE_WINDOW,
    PAPER_QUEUE_CONTRACTS,
    _grounded_tool_payload,
    _insert_paper_row,
    _reserve_spend,
    brier_report,
    call_reserve_usd,
    comparison_summary,
    contract_fee_net_dollars,
    DEFAULT_DAILY_USD_CAP,
    MEASURED_CYCLE_COST_USD,
    edge_scan_interval_seconds,
    ev_by_edge_bucket,
    exit_versus_hold,
    grok_max_search_results,
    _list_block,
    cache_settlement_fee_net,
    paper_position_rows,
    paper_queue_decision,
    politics_scan_series,
    promotion_label,
    rank_politics_markets,
    run_paper_edge_scan,
    run_paper_exit_watch,
    score_open_positions,
    scorecard_interval,
    stable_scan_probability,
    write_daily_edge_report,
    write_paper_scorecard,
    _strike_text,
)
from utils.event_news_research import (
    EVENT_NEWS_COHORT_ID,
    event_news_fee_net_cents,
    event_news_fee_net_edge,
)
from utils.series_universe import event_news_live_post_block_reason
from utils.event_news_grok_flags import event_news_grok_grounding_for_mode
from utils.event_news_grok_veto import GROK_MODEL, XAI_API_KEY_ENV
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
ALLOW = ("KXTRUMPSAY",)


class _Response:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def json(self) -> dict:
        return self._body


def _seed_pnl(directory: Path, fee_net: str, close: str = "2026-10-17T00:00:00Z") -> None:
    payload = {"rows": [{"close": close, "fee_net": fee_net, "status": "settled"}]}
    (directory / "grok_settled_fee_net.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


def _paths(tmp_path: Path) -> dict:
    return {
        "call_log_path": tmp_path / "calls.jsonl",
        "candidate_log_path": tmp_path / "candidates.jsonl",
        "comparison_log_path": tmp_path / "comparisons.jsonl",
        "spend_path": tmp_path / "spend.json",
        "cache_path": tmp_path / "cache.json",
        "screen_log_path": tmp_path / "screen.jsonl",
    }


def _market(**overrides) -> dict:
    market = {
        "ticker": "KXTRUMPSAY-26OCT20-TEST",
        "series": "KXTRUMPSAY",
        "status": "active",
        "yes_ask": 55,
        "no_ask": 46,
        "title": "Will Trump say the word",
        "rules": "Official transcript",
        "close_time": "2026-10-20T23:00:00Z",
    }
    market.update(overrides)
    return market


def _body(probability: str, *, sources: list[str] | None, grounded: bool) -> dict:
    payload = {
        "probability": probability,
        "confidence": "0.80",
        "verdict": "YES",
        "sources": sources or [],
    }
    text = json.dumps(payload)
    body: dict = {"output_text": "note {not json} " + text}
    if grounded and sources:
        body["citations"] = list(sources)
    return body


def _poster(calls: list[dict], *, grounded_prob: str = "0.70", ungrounded_prob: str = "0.72"):
    def post(url, json, headers, timeout):  # noqa: A002 - matches requests.post
        calls.append(
            {
                "url": url,
                "payload": json,
                "timeout": timeout,
                "authorization": headers.get("Authorization"),
            }
        )
        grounded = "tools" in json
        probability = grounded_prob if grounded else ungrounded_prob
        sources = ["https://example.test/source"] if grounded else []
        return _Response(_body(probability, sources=sources, grounded=grounded))

    return post


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _scan(tmp_path: Path, markets, post, environ, prior_prob: str | None = "0.70", **extra) -> dict:
    """One same-day sample is not a queue. The default prior is that first sample."""
    payload = {
        "live": False,
        "allowlist": ALLOW,
        "environ": environ,
        "post": post,
        "now": NOW,
        "raw_dir": tmp_path / "raw",
        "post_lookup": lambda: "post-1",
    }
    payload.update(_paths(tmp_path))
    payload.update(extra)
    cache = payload["cache_path"]
    if prior_prob is not None and not cache.exists():
        tickers = {}
        for market in markets:
            if not isinstance(market, dict):
                continue
            ticker = str(market.get("ticker") or "")
            if not ticker:
                continue
            tickers[ticker] = {
                "ts": "2026-10-17T11:00:00Z",
                "prob": prior_prob,
                "confidence": "0.80",
                "sources": ["https://example.test/prior"],
                "yes_ask": market.get("yes_ask"),
                "no_ask": market.get("no_ask"),
                "post_id": "post-1",
                "series": market.get("series") or "",
                "summary": "prior",
            }
        if tickers:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"tickers": tickers}) + "\n", encoding="utf-8")
    return run_paper_edge_scan(markets, **payload)


def _fee(probability: str, ask: int, contracts: int = 1):
    scored = event_news_fee_net_edge(Decimal(probability), ask, contracts)
    assert scored is not None
    return scored


def test_quote_fee_is_exact_not_a_whole_cent_ceiling() -> None:
    """50c is 0.0175 exactly. 55c ceils 0.017325 to a centicent, 0.0174."""
    assert _fee("0.50", 50)[1] == Decimal("0.0175")
    assert _fee("0.50", 55)[1] == Decimal("0.0174")
    assert event_news_fee_net_edge(True, 55, 1) is None
    assert event_news_fee_net_edge(Decimal("0.50"), 0, 1) is None
    assert event_news_fee_net_edge(Decimal("0.50"), 100, 1) is None


def test_fee_net_edge_uses_yaml_minimum_not_an_8c_gate() -> None:
    """0.64 at 55c clears 0.04 after the exact fee. 0.58 does not."""
    high = _fee("0.70", 55)
    mid = _fee("0.64", 55)
    low = _fee("0.58", 55)
    assert high[2] == Decimal("0.70") - Decimal("0.55") - Decimal("0.0174")
    assert mid[2] == Decimal("0.0726")
    assert low[2] == Decimal("0.0126")
    minimum = Decimal("0.04")
    queued, edge = paper_queue_decision(
        probability=Decimal("0.70"),
        ask_cents=55,
        side="yes",
        contracts=1,
        edge_min=minimum,
    )
    also, mid_cents = paper_queue_decision(
        probability=Decimal("0.64"),
        ask_cents=55,
        side="yes",
        contracts=1,
        edge_min=minimum,
    )
    skipped, small = paper_queue_decision(
        probability=Decimal("0.58"),
        ask_cents=55,
        side="yes",
        contracts=1,
        edge_min=minimum,
    )
    assert queued == "queue"
    assert edge == 13
    assert also == "queue"
    assert mid_cents == 7
    assert skipped == "edge"
    assert small == 1


def test_yes_floor_and_band_block_before_a_large_probability() -> None:
    reason, _edge = paper_queue_decision(
        probability=Decimal("0.99"),
        ask_cents=54,
        side="yes",
        contracts=1,
    )
    assert reason == "yes_floor"
    band, _edge = paper_queue_decision(
        probability=Decimal("0.99"),
        ask_cents=35,
        side="yes",
        contracts=1,
    )
    assert band == "ask_band"
    no_band, _edge = paper_queue_decision(
        probability=Decimal("0.99"),
        ask_cents=35,
        side="no",
        contracts=1,
    )
    assert no_band == "ask_band"
    no_queue, no_edge = paper_queue_decision(
        probability=Decimal("0.90"),
        ask_cents=36,
        side="no",
        contracts=1,
        edge_min=Decimal("0.04"),
    )
    assert no_queue == "queue"
    assert no_edge is not None and no_edge >= 8
    yes_at_band, _edge = paper_queue_decision(
        probability=Decimal("0.99"),
        ask_cents=36,
        side="yes",
        contracts=1,
    )
    assert yes_at_band == "yes_floor"


def test_size_above_8_is_a_skip_not_a_clamp() -> None:
    reason, edge = paper_queue_decision(
        probability=Decimal("0.90"),
        ask_cents=55,
        side="yes",
        contracts=9,
    )
    assert reason == "size_cap"
    assert edge is None


def test_yaml_edge_min_skips_after_the_screen(tmp_path: Path) -> None:
    """0.70 clears the 5c screen. Grounded 0.58 is under the yaml 0.04 fee-net."""
    calls: list[dict] = []
    counts = _scan(
        tmp_path,
        [_market()],
        _poster(calls, grounded_prob="0.58", ungrounded_prob="0.70"),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob="0.58",
    )
    assert counts["queued"] == 0
    assert counts["skipped"] == 1
    assert counts["grounded_calls"] == 1
    candidate = _rows(tmp_path / "candidates.jsonl")[0]
    assert candidate["queued"] is False
    assert candidate["skip_reason"] == "edge"
    assert candidate["contracts"] == PAPER_QUEUE_CONTRACTS


def test_grounded_scan_queues_four_and_records_the_disabled_proposal(tmp_path: Path) -> None:
    calls: list[dict] = []
    counts = _scan(
        tmp_path,
        [_market(), _market(ticker="KXTRUMPSAY-26OCT20-SECOND")],
        _poster(calls),
        {XAI_API_KEY_ENV: "test-key"},
    )
    assert counts == {
        "examined": 1,
        "queued": 1,
        "skipped": 0,
        "screen_skipped": 0,
        "cache_hits": 0,
        "grounded_calls": 1,
    }
    assert len(calls) == 2
    ungrounded, grounded = calls
    assert "tools" not in ungrounded["payload"]
    web_tool, x_tool = grounded["payload"]["tools"]
    assert web_tool == {"type": "web_search"}
    assert x_tool["type"] == "x_search"
    assert x_tool["from_date"] == "2026-10-14"
    assert x_tool["to_date"] == "2026-10-18"
    assert x_tool["allowed_x_handles"] == ["realDonaldTrump", "POTUS", "WhiteHouse"]
    assert "max_search_results" not in x_tool
    assert grounded["payload"]["max_turns"] == 2
    schema = grounded["payload"]["text"]["format"]
    assert schema["type"] == "json_schema"
    assert schema["name"] == "grok_series_context"
    assert schema["strict"] is True
    assert schema["schema"]["required"] == ["latest_post_id", "summary", "sources", "markets"]
    assert grounded["payload"]["model"] == GROK_MODEL
    assert grounded["timeout"] == GROK_EDGE_TIMEOUT_SECONDS == 20.0
    assert ungrounded["timeout"] == 20.0
    assert grounded["payload"]["temperature"] == 0
    assert "Use at most 8 sources." in grounded["payload"]["input"][0]["content"]
    user_prompt = grounded["payload"]["input"][1]["content"]
    assert "strike=" in user_prompt
    assert "rules=Official transcript" in user_prompt
    assert "close=2026-10-20T23:00:00Z" in user_prompt
    candidate = _rows(tmp_path / "candidates.jsonl")[0]
    assert candidate["queued"] is True
    assert candidate["contracts"] == 4
    assert "sizing_proposal_enabled" not in candidate
    assert "sizing_proposal_contracts" not in candidate
    assert candidate["edge_cents"] == 13
    assert candidate["sources"] == ["https://example.test/source"]
    assert "test-key" not in (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    comparison = _rows(tmp_path / "comparisons.jsonl")[0]
    assert comparison["agree"] is True
    logged = _rows(tmp_path / "calls.jsonl")
    assert logged[0]["grounded"] is False
    assert logged[0]["tools"] == []
    assert len(logged[1]["prompt_hash"]) == 16
    assert logged[1]["tools"] == ["web_search", "x_search"]
    assert logged[1]["max_search_results"] == 8
    assert logged[1]["max_turns"] == 2


def test_open_status_is_not_a_tradeable_response(tmp_path: Path) -> None:
    """The request filter is status=open. The tradeable response field is active."""
    calls: list[dict] = []
    counts = _scan(
        tmp_path,
        [_market(status="open")],
        _poster(calls),
        {XAI_API_KEY_ENV: "test-key"},
    )
    assert counts["examined"] == 0
    assert calls == []


def test_malformed_grounded_reply_does_not_queue(tmp_path: Path) -> None:
    def post(url, json, headers, timeout):  # noqa: A002
        if "tools" in json:
            return _Response({"output_text": "not json"})
        return _Response(_body("0.70", sources=[], grounded=False))

    counts = _scan(tmp_path, [_market()], post, {XAI_API_KEY_ENV: "test-key"})
    assert counts["queued"] == 0
    decision = _rows(tmp_path / "candidates.jsonl")
    assert [row["reason_code"] for row in decision] == ["malformed"]
    assert decision[0]["queued"] is False
    assert decision[0]["grok_probability"] is None
    rows = _rows(tmp_path / "calls.jsonl")
    assert rows[0]["grounded"] is False
    assert rows[0]["skip_reason"] is None
    grounded = [row for row in rows if row["grounded"]]
    assert [row["skip_reason"] for row in grounded] == ["malformed", "malformed"]


def test_grounded_reply_without_a_source_does_not_queue(tmp_path: Path) -> None:
    def post(url, json, headers, timeout):  # noqa: A002
        return _Response(_body("0.80", sources=[], grounded=False))

    counts = _scan(tmp_path, [_market()], post, {XAI_API_KEY_ENV: "test-key"})
    assert counts["queued"] == 0
    grounded = [row for row in _rows(tmp_path / "calls.jsonl") if row["grounded"]]
    assert grounded
    assert all(row["skip_reason"] == "malformed" for row in grounded)


def test_timeout_keeps_the_reserve_and_does_not_queue(tmp_path: Path) -> None:
    """A screen timeout is not a reason to pay for the grounded call."""

    def post(url, json, headers, timeout):  # noqa: A002
        raise requests.Timeout("late")

    counts = _scan(tmp_path, [_market()], post, {XAI_API_KEY_ENV: "test-key"})
    assert counts["queued"] == 0
    assert counts["screen_skipped"] == 1
    assert counts["grounded_calls"] == 0
    spent = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert Decimal(spent["spent_usd"]) == MIN_CALL_RESERVE_USD
    assert _rows(tmp_path / "calls.jsonl")[0]["skip_reason"] == "timeout"
    decision = _rows(tmp_path / "candidates.jsonl")
    assert [row["reason_code"] for row in decision] == ["timeout"]
    assert decision[0]["queued"] is False
    assert decision[0]["grok_probability"] is None


def test_spend_cap_skips_before_the_post(tmp_path: Path) -> None:
    """The cheap screen still fits under $2. The grounded series reserve does not."""
    (tmp_path / "spend.json").write_text(
        json.dumps({"date": "2026-10-17", "spent_usd": "1.90"}) + "\n",
        encoding="utf-8",
    )
    _seed_pnl(tmp_path, "12")
    history = {
        "cost_source": "provider",
        "grounded": True,
        "cost_usd": "0.40",
    }
    (tmp_path / "calls.jsonl").write_text(json.dumps(history) + "\n", encoding="utf-8")
    calls: list[dict] = []
    counts = _scan(tmp_path, [_market()], _poster(calls), {XAI_API_KEY_ENV: "test-key"})
    assert len(calls) == 1
    assert "tools" not in calls[0]["payload"]
    assert counts["queued"] == 0
    logged = [row for row in _rows(tmp_path / "calls.jsonl") if row.get("ticker")]
    assert logged[0]["grounded"] is False
    assert logged[0]["skip_reason"] is None
    assert logged[1]["grounded"] is True
    assert logged[1]["skip_reason"] == "spend_cap"


def _close_in(hours: int) -> str:
    return (NOW + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_rank_keeps_politics_inside_72h_and_drops_kxdji() -> None:
    """Response status open, a 95c ask, and KXDJI are not scan candidates."""
    markets = [
        _market(ticker="KXTRUMPSAY-SOON", close_time=_close_in(24), volume=50),
        _market(ticker="KXTRUMPSAY-THIN", close_time=_close_in(24), volume=0),
        _market(ticker="KXTRUMPSAY-OPEN", close_time=_close_in(24), status="open", volume=999),
        _market(ticker="KXTRUMPSAY-WIDE", close_time=_close_in(24), yes_ask=95, no_ask=6),
        _market(ticker="KXTRUMPSAY-CHEAP", close_time=_close_in(24), yes_ask=20, no_ask=35),
        _market(ticker="KXTRUMPSAY-LATER", close_time=_close_in(96), volume=5000),
        _market(
            ticker="KXDJI-26OCT19-51550",
            series="KXDJI",
            close_time=_close_in(24),
            volume=9000,
        ),
        _market(ticker="KXTRUMPSAY-EDGE", close_time=_close_in(72), yes_ask=94, no_ask=7, volume=5),
    ]
    ranked = rank_politics_markets(markets, now=NOW, series=politics_scan_series())
    tickers = [row["ticker"] for row in ranked]
    assert tickers == ["KXTRUMPSAY-SOON", "KXTRUMPSAY-EDGE", "KXTRUMPSAY-THIN"]
    scanned = politics_scan_series(("KXDJI", "KXNEWPOL"), now=NOW)
    from utils.series_universe import get_universe, live_series_tickers

    yaml_names = tuple(
        entry.series
        for entry in get_universe().entries
        if entry.mode != "off" and not entry.series.startswith("PM")
    )
    assert scanned == yaml_names
    assert set(live_series_tickers()) < set(scanned)
    assert len(live_series_tickers()) == 13
    assert "KXDJI" not in scanned
    assert "KXNEWPOL" not in scanned
    assert "KXTRUMPSAY" in scanned
    assert "KXHORMUZWEEKLY" in scanned


def _responses_shape(text: str, *, annotation: str | None = None) -> dict:
    """Responses payload whose blank output_text used to hide the message JSON.

    This is the shape that produced a malformed skip. It is not a byte copy
    of the 2026-10-18 KXDJI reply. That body was not saved.
    """
    part: dict = {"type": "output_text", "text": text}
    if annotation:
        part["annotations"] = [{"type": "url_citation", "url": annotation}]
    return {
        "output_text": "",
        "output": [{"type": "message", "content": [part]}],
    }


def test_blank_output_text_parses_prob_and_ignores_trailing_tool_args(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "prob": 0.70,
            "confidence": 0.80,
            "sources": ["https://example.test/source"],
            "rationale": "public schedule",
        }
    )
    tool_args = json.dumps({"query": "trump", "tool": "web_search"})

    def post(url, json, headers, timeout):  # noqa: A002
        grounded = "tools" in json
        if grounded:
            return _Response(
                _responses_shape(payload + "\n" + tool_args, annotation="https://example.test/source")
            )
        return _Response(_body("0.72", sources=[], grounded=False))

    counts = _scan(tmp_path, [_market()], post, {XAI_API_KEY_ENV: "test-key"})
    assert counts["queued"] == 1
    logged = [row for row in _rows(tmp_path / "calls.jsonl") if row["grounded"]][-1]
    assert logged["sources"] == ["https://example.test/source"]
    assert logged["rationale"] == "public schedule"
    candidate = _rows(tmp_path / "candidates.jsonl")[0]
    assert Decimal(candidate["grok_probability"]) == Decimal("0.70")


def test_annotation_url_is_enough_for_a_grounded_source(tmp_path: Path) -> None:
    text = json.dumps(
        {"prob": 0.70, "confidence": 0.80, "sources": [], "rationale": "annotation only"}
    )

    def post(url, json, headers, timeout):  # noqa: A002
        if "tools" in json:
            return _Response(_responses_shape(text, annotation="https://example.test/ann"))
        return _Response(_body("0.72", sources=[], grounded=False))

    counts = _scan(tmp_path, [_market()], post, {XAI_API_KEY_ENV: "test-key"})
    assert counts["queued"] == 1
    grounded = [row for row in _rows(tmp_path / "calls.jsonl") if row["grounded"]]
    assert grounded[-1]["sources"] == ["https://example.test/ann"]


def test_malformed_reply_retries_once_then_skips_and_redacts_the_raw_body(tmp_path: Path) -> None:
    def post(url, json, headers, timeout):  # noqa: A002
        if "tools" in json:
            return _Response(
                {
                    "output_text": "not json",
                    "api_key": "secret-value",
                    "note": "Bearer supersecret sk-live-secret xai-live-secret",
                }
            )
        return _Response(_body("0.70", sources=[], grounded=False))

    counts = _scan(tmp_path, [_market()], post, {XAI_API_KEY_ENV: "test-key"})
    assert counts["queued"] == 0
    grounded = [row for row in _rows(tmp_path / "calls.jsonl") if row["grounded"]]
    assert [row["attempt"] for row in grounded] == [1, 2]
    assert all(row["skip_reason"] == "malformed" for row in grounded)
    raw_files = list((tmp_path / "raw").glob("*.json"))
    assert len(raw_files) == 2
    saved = "\n".join(path.read_text(encoding="utf-8") for path in raw_files)
    assert "secret-value" not in saved
    assert "sk-live-secret" not in saved
    assert "xai-live-secret" not in saved
    assert "supersecret" not in saved
    assert "[redacted]" in saved


def test_retry_success_uses_the_second_probability(tmp_path: Path) -> None:
    attempts = {"n": 0}

    def post(url, json, headers, timeout):  # noqa: A002
        if "tools" in json:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return _Response({"output_text": "not json"})
            return _Response(_body("0.70", sources=["https://example.test/source"], grounded=True))
        return _Response(_body("0.72", sources=[], grounded=False))

    counts = _scan(tmp_path, [_market()], post, {XAI_API_KEY_ENV: "test-key"})
    assert counts["queued"] == 1
    grounded = [row for row in _rows(tmp_path / "calls.jsonl") if row["grounded"]]
    assert grounded[0]["skip_reason"] == "malformed"
    assert grounded[1]["attempt"] == 2
    assert grounded[1]["skip_reason"] is None
    candidate = _rows(tmp_path / "candidates.jsonl")[0]
    assert Decimal(candidate["grok_probability"]) == Decimal("0.70")
    assert len(list((tmp_path / "raw").glob("*.json"))) == 1


def test_cost_ticks_replace_the_reserve(tmp_path: Path) -> None:
    """37756000 ticks is $0.0037756. A missing usage field keeps the reserve."""

    def with_ticks(ticks):
        def post(url, json, headers, timeout):  # noqa: A002
            grounded = "tools" in json
            body = _body(
                "0.70",
                sources=["https://example.test/source"] if grounded else [],
                grounded=grounded,
            )
            if ticks is not None:
                body["usage"] = {
                    "cost_in_usd_ticks": ticks if grounded else 0,
                    "input_tokens": 12,
                    "output_tokens": 4,
                }
            return _Response(body)

        return post

    counted = _scan(tmp_path / "ticks", [_market()], with_ticks(37756000), {XAI_API_KEY_ENV: "test-key"})
    assert counted["queued"] == 1
    grounded_rows = [row for row in _rows(tmp_path / "ticks" / "calls.jsonl") if row["grounded"]]
    grounded = grounded_rows[0]
    assert grounded["cost_source"] == "provider"
    assert Decimal(grounded["cost_usd"]) == Decimal("0.0037756")
    assert grounded["cost_in_usd_ticks"] == 37756000
    assert grounded["input_tokens"] == 12
    spent = json.loads((tmp_path / "ticks" / "spend.json").read_text(encoding="utf-8"))
    assert Decimal(spent["spent_usd"]) == Decimal("0.0037756")

    zero = _scan(tmp_path / "zero-ticks", [_market()], with_ticks(0), {XAI_API_KEY_ENV: "test-key"})
    assert zero["queued"] == 1
    zero_spent = json.loads((tmp_path / "zero-ticks" / "spend.json").read_text(encoding="utf-8"))
    assert Decimal(zero_spent["spent_usd"]) == Decimal("0")

    missing = _scan(tmp_path / "missing", [_market()], with_ticks(None), {XAI_API_KEY_ENV: "test-key"})
    assert missing["queued"] == 1
    missing_rows = _rows(tmp_path / "missing" / "calls.jsonl")
    assert missing_rows[0]["cost_source"] == "reserve"
    assert Decimal(missing_rows[0]["cost_usd"]) == MIN_CALL_RESERVE_USD
    missing_spent = json.loads((tmp_path / "missing" / "spend.json").read_text(encoding="utf-8"))
    assert Decimal(missing_spent["spent_usd"]) == MIN_CALL_RESERVE_USD * 2


def test_measured_p90_sets_the_next_reserve(tmp_path: Path) -> None:
    """Five provider costs of $0.20 reserve 0.30. A zero-tick screen refunds its nickel."""
    lines = [
        json.dumps({"cost_source": "provider", "grounded": True, "cost_usd": "0.20"})
        for _ in range(5)
    ]
    (tmp_path / "calls.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert call_reserve_usd(tmp_path / "calls.jsonl", grounded=True) == Decimal("0.30")
    assert call_reserve_usd(tmp_path / "calls.jsonl", grounded=False) == MIN_CALL_RESERVE_USD
    calls: list[dict] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(
            {"url": url, "payload": json, "timeout": timeout, "authorization": headers.get("Authorization")}
        )
        grounded = "tools" in json
        body = _body(
            "0.70",
            sources=["https://example.test/source"] if grounded else [],
            grounded=grounded,
        )
        if not grounded:
            body["usage"] = {"cost_in_usd_ticks": 0, "input_tokens": 1, "output_tokens": 1}
        return _Response(body)

    counts = _scan(
        tmp_path,
        [_market()],
        post,
        {XAI_API_KEY_ENV: "test-key", "GROK_DAILY_USD_CAP": "0.30"},
    )
    assert len(calls) == 2
    assert "tools" not in calls[0]["payload"]
    assert "tools" in calls[1]["payload"]
    assert counts["queued"] == 1


def test_missing_key_does_not_post(tmp_path: Path) -> None:
    calls: list[dict] = []
    counts = _scan(tmp_path, [_market()], _poster(calls), {})
    assert calls == []
    assert counts["queued"] == 0
    assert _rows(tmp_path / "calls.jsonl")[0]["skip_reason"] == "missing_key"
    assert not (tmp_path / "spend.json").exists()


def test_corrupt_spend_file_fails_closed(tmp_path: Path) -> None:
    calls: list[dict] = []
    (tmp_path / "spend.json").write_text("not-json", encoding="utf-8")
    counts = _scan(tmp_path, [_market()], _poster(calls), {XAI_API_KEY_ENV: "test-key"})
    assert calls == []
    assert counts["queued"] == 0


def test_zero_cap_and_explicit_false_queue_nothing(tmp_path: Path) -> None:
    calls: list[dict] = []
    (tmp_path / "zero").mkdir()
    zero = _scan(
        tmp_path / "zero",
        [_market()],
        _poster(calls),
        {XAI_API_KEY_ENV: "test-key", "GROK_DAILY_USD_CAP": "0"},
    )
    assert zero["queued"] == 0
    disabled = run_paper_edge_scan(
        [_market()],
        live=False,
        allowlist=ALLOW,
        environ={XAI_API_KEY_ENV: "test-key", EVENT_NEWS_GROK_EDGE_SCAN: "false"},
        post=_poster(calls),
        now=NOW,
        **_paths(tmp_path),
    )
    assert disabled == {"examined": 0, "queued": 0, "skipped": 0}
    assert calls == []


def test_live_true_does_not_call_even_when_the_flag_is_true(tmp_path: Path) -> None:
    calls: list[dict] = []
    counts = run_paper_edge_scan(
        [_market()],
        live=True,
        allowlist=ALLOW,
        environ={XAI_API_KEY_ENV: "test-key", EVENT_NEWS_GROK_EDGE_SCAN: "true"},
        post=_poster(calls),
        now=NOW,
        **_paths(tmp_path),
    )
    assert counts == {"examined": 0, "queued": 0, "skipped": 0}
    assert calls == []


def test_comparison_brier_and_ev_need_settled_rows() -> None:
    empty = comparison_summary([])
    assert empty["n"] == 0
    assert empty["status"] == "not_enough_data"
    agreed = comparison_summary(
        [
            {
                "grounded_probability": "0.70",
                "ungrounded_probability": "0.74",
                "outcome": 1,
            },
            {
                "grounded_probability": "0.20",
                "ungrounded_probability": "0.80",
                "outcome": 0,
            },
        ]
    )
    assert agreed["n"] == 2
    assert agreed["agree_pct"] == Decimal("50")
    assert agreed["grounded_hits"] == 1
    assert agreed["ungrounded_hits"] == 1
    tie = comparison_summary(
        [{"grounded_probability": "0.5", "ungrounded_probability": "0.5", "outcome": 1}]
    )
    assert tie["grounded_hits"] == 1
    assert tie["ungrounded_hits"] == 1
    assert brier_report([])["status"] == "not_enough_data"
    scored = brier_report(
        [
            {"probability": "1", "outcome": 1},
            {"probability": "0", "outcome": 1},
        ]
    )
    assert scored["brier"] == Decimal("0.5")
    assert scored["buckets"][4]["n"] == 1
    assert ev_by_edge_bucket([])["status"] == "not_enough_data"
    thin = ev_by_edge_bucket([{"edge_cents": 8, "pnl_dollars": "1.0"}])
    assert thin["buckets"][1]["status"] == "not_enough_data"
    rich = ev_by_edge_bucket(
        [
            {"edge_cents": 9, "pnl_dollars": "1"},
            {"edge_cents": 10, "pnl_dollars": "3"},
        ]
    )
    bucket = rich["buckets"][1]
    assert bucket["status"] == "ok"
    assert bucket["mean_ev"] == Decimal("2")
    assert bucket["ci95_low"] == Decimal("2") - Decimal("1.96")
    assert bucket["ci95_high"] == Decimal("2") + Decimal("1.96")


def test_exit_logs_the_bid_and_does_not_call_or_order(tmp_path: Path) -> None:
    """A resting bid is a mark. The exit watch does not post or write an exit row."""
    calls: list[dict] = []
    marks = tmp_path / "marks.jsonl"
    logged = run_paper_exit_watch(
        [{"ticker": "KXTRUMPSAY-26OCT20-TEST", "side": "yes"}],
        live=False,
        market_lookup=lambda ticker: {"yes_bid": 60, "no_bid": 40},
        environ={XAI_API_KEY_ENV: "test-key"},
        post=_poster(calls),
        call_log_path=tmp_path / "calls.jsonl",
        exit_log_path=tmp_path / "exits.jsonl",
        mark_log_path=marks,
        spend_path=tmp_path / "spend.json",
        now=NOW,
    )
    assert logged == 1
    assert calls == []
    assert not (tmp_path / "exits.jsonl").exists()
    assert not (tmp_path / "calls.jsonl").exists()
    row = _rows(marks)[0]
    assert row["bid_cents"] == 60
    assert row["paid"] is False
    compared = exit_versus_hold(
        side="yes",
        bid_cents=60,
        settlement_yes=True,
        contracts=1,
        entry_ask_cents=55,
    )
    assert compared == {"exit_cents": 2, "hold_cents": 43}
    held_loss = exit_versus_hold(
        side="yes",
        bid_cents=60,
        settlement_yes=False,
        contracts=1,
        entry_ask_cents=55,
    )
    assert held_loss == {"exit_cents": 2, "hold_cents": -57}


def json_dumps_contrary(contrary: bool) -> str:
    return json.dumps(
        {
            "contrary": contrary,
            "confidence": 0.91,
            "probability": 0.2,
            "sources": ["https://example.test/contrary"],
        }
    )


def _empty_yes_book(ticker: str) -> dict:
    """Live shape: YES ask is the string 1.0000 with size 0.00. The bid is 0.9900."""
    return {
        "ticker": ticker,
        "series": ticker.split("-", 1)[0],
        "status": "active",
        "yes_ask_dollars": "1.0000",
        "yes_ask_size_fp": "0.00",
        "yes_bid_dollars": "0.9900",
        "no_ask_dollars": "0.0100",
        "no_bid_dollars": "0.0000",
        "title": ticker,
        "rules": "",
        "close_time": "2026-10-24T23:00:00Z",
    }


def test_empty_book_string_ask_uses_the_bid_integer(tmp_path: Path) -> None:
    """A $1.00 empty ask is not an integer quote. The score uses the 99c bid."""
    from utils.event_news_grok_edge import _mark_cents

    t6 = {
        "ticker": "KXTRUMPACT-26OCT18-T6",
        "position_fp": "8.00",
        "market_exposure_dollars": "5.350000",
    }
    t240 = {
        "ticker": "KXTRUTHSOCIAL-26OCT24-T240",
        "position_fp": "8.00",
        "market_exposure_dollars": "4.880000",
    }
    book = _empty_yes_book(t6["ticker"])
    assert _mark_cents(book, "yes", t6) == 99
    assert type(_mark_cents(book, "yes", t6)) is int
    assert _mark_cents({"yes_ask": 55, "yes_bid": 50}, "yes", t6) == 55
    assert _mark_cents({}, "yes", t240) == 61
    assert _mark_cents({}, "yes", t6) == 67
    quoted = SimpleNamespace(yes_ask_cents=100, yes_bid_cents=99, yes_ask_size_fp="0.00")
    assert _mark_cents(quoted, "yes", t6) == 99
    _seed_cache(
        tmp_path / "cache.json",
        t6["ticker"],
        series="KXTRUMPACT",
        yes_ask=None,
        no_ask=None,
    )
    calls: list[int] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(1)
        raise AssertionError("no paid call")

    scored = score_open_positions(
        [t6],
        live=False,
        market_lookup=lambda ticker: book,
        environ={XAI_API_KEY_ENV: "test-key", "GROK_DAILY_USD_CAP": "3"},
        post=post,
        now=NOW,
        call_log_path=tmp_path / "calls.jsonl",
        score_log_path=tmp_path / "scores.jsonl",
        spend_path=tmp_path / "spend.json",
        cache_path=tmp_path / "cache.json",
        screen_log_path=tmp_path / "screen.jsonl",
        post_lookup=lambda: "post-1",
    )
    assert calls == []
    row = _rows(tmp_path / "scores.jsonl")[0]
    assert row["ask_cents"] == 99
    assert type(row["ask_cents"]) is int
    assert row["skip_reason"] != "bad_ask"
    assert scored["scored"] == 1


def test_empty_book_cache_miss_does_not_pay(tmp_path: Path) -> None:
    """Size 0 on both sides cannot be entered, so the score makes no Grok call."""
    book = _empty_yes_book("KXTRUMPACT-26OCT18-T6")
    calls: list[int] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(1)
        raise AssertionError("no paid call")

    scored = score_open_positions(
        [{
            "ticker": book["ticker"],
            "position_fp": "8.00",
            "market_exposure_dollars": "5.350000",
        }],
        live=False,
        market_lookup=lambda ticker: book,
        environ={XAI_API_KEY_ENV: "test-key", "GROK_DAILY_USD_CAP": "3"},
        post=post,
        now=NOW,
        call_log_path=tmp_path / "calls.jsonl",
        score_log_path=tmp_path / "scores.jsonl",
        spend_path=tmp_path / "spend.json",
        cache_path=tmp_path / "cache.json",
        screen_log_path=tmp_path / "screen.jsonl",
        post_lookup=lambda: "post-1",
    )
    assert calls == []
    assert scored["grounded_calls"] == 0
    assert _rows(tmp_path / "scores.jsonl")[0]["skip_reason"] == "depth"


def test_untradeable_scan_makes_no_paid_call(tmp_path: Path) -> None:
    """Ask band, empty depth, the sports blocklist, and an unknown series post nothing."""
    env = {XAI_API_KEY_ENV: "test-key", "GROK_DAILY_USD_CAP": "3"}
    calls: list[dict] = []
    band = _scan(
        tmp_path / "band",
        [_market(yes_ask=94, no_ask=8)],
        _poster(calls),
        env,
        prior_prob=None,
    )
    assert calls == []
    assert band["grounded_calls"] == 0
    assert _rows(tmp_path / "band" / "candidates.jsonl")[0]["skip_reason"] == "near_certain_ask"
    depth = _scan(
        tmp_path / "depth",
        [_market(yes_ask_size_fp="0", no_ask_size_fp="0.00")],
        _poster(calls),
        env,
        prior_prob=None,
    )
    assert calls == []
    assert depth["grounded_calls"] == 0
    assert _rows(tmp_path / "depth" / "candidates.jsonl")[0]["skip_reason"] == "depth"
    sports = _scan(
        tmp_path / "sports",
        [_market(ticker="KXNFLGAME-26OCT20-TEST", series="KXNFLGAME")],
        _poster(calls),
        env,
        prior_prob=None,
        allowlist=("KXNFLGAME",),
    )
    assert calls == []
    assert _rows(tmp_path / "sports" / "candidates.jsonl")[0]["skip_reason"] == "blocklist"
    unknown = _scan(
        tmp_path / "unknown",
        [_market(ticker="ZZNOTASERIES-26OCT20-TEST", series="ZZNOTASERIES")],
        _poster(calls),
        env,
        prior_prob=None,
        allowlist=("ZZNOTASERIES",),
    )
    assert calls == []
    assert _rows(tmp_path / "unknown" / "candidates.jsonl")[0]["skip_reason"] == "universe"


def test_exit_watch_logs_both_open_books_without_a_paid_call(tmp_path: Path) -> None:
    """Both open books get a mark. The ask string is not the bid, and nothing is posted."""
    books = {
        "KXTRUMPACT-26OCT18-T6": _empty_yes_book("KXTRUMPACT-26OCT18-T6"),
        "KXTRUTHSOCIAL-26OCT24-T240": SimpleNamespace(
            yes_ask_cents=100,
            yes_bid_cents=99,
            yes_ask_size_fp="0.00",
        ),
    }
    calls: list[str] = []
    marks = tmp_path / "marks.jsonl"
    logged = run_paper_exit_watch(
        [
            {"ticker": "KXTRUMPACT-26OCT18-T6", "side": "yes"},
            {"ticker": "KXTRUTHSOCIAL-26OCT24-T240", "side": "yes"},
        ],
        live=False,
        market_lookup=lambda ticker: books[ticker],
        environ={XAI_API_KEY_ENV: "test-key", "GROK_DAILY_USD_CAP": "3"},
        post=_poster(calls),
        call_log_path=tmp_path / "calls.jsonl",
        exit_log_path=tmp_path / "grok_edge_exits.jsonl",
        mark_log_path=marks,
        spend_path=tmp_path / "spend.json",
        raw_dir=tmp_path / "raw",
        now=NOW,
    )
    assert logged == 2
    assert calls == []
    assert not (tmp_path / "grok_edge_exits.jsonl").exists()
    rows = _rows(marks)
    assert [row["ticker"] for row in rows] == [
        "KXTRUMPACT-26OCT18-T6",
        "KXTRUTHSOCIAL-26OCT24-T240",
    ]
    assert [row["bid_cents"] for row in rows] == [99, 99]
    assert all(type(row["bid_cents"]) is int and row["paid"] is False for row in rows)


def test_series_and_scans_leave_one_dollar_for_exit(tmp_path: Path) -> None:
    """Past $2, series and scans do not post. An exit call still fits under the $3 cap."""
    from utils.event_news_grok_edge import _call_grok

    spend = tmp_path / "spend.json"
    spend.write_text(
        json.dumps({"date": "2026-10-17", "spent_usd": "2.01"}) + "\n",
        encoding="utf-8",
    )
    _seed_pnl(tmp_path, "12")
    calls: list[str] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append("post")
        return _Response(
            {"output_text": json_dumps_contrary(True), "citations": ["https://example.test/contrary"]}
        )

    env = {XAI_API_KEY_ENV: "test-key", GROK_DAILY_USD_CAP: "3"}
    series = _call_grok(
        ticker="KXTRUMPACT",
        prompt="series",
        system="s",
        grounded=False,
        environ=env,
        post=post,
        call_log_path=tmp_path / "series.jsonl",
        spend_path=spend,
        raw_dir=tmp_path / "raw",
        now=NOW,
        kind="series",
    )
    scan = _call_grok(
        ticker="KXTRUMPACT-26OCT18-T6",
        prompt="scan",
        system="s",
        grounded=False,
        environ=env,
        post=post,
        call_log_path=tmp_path / "scan.jsonl",
        spend_path=spend,
        raw_dir=tmp_path / "raw",
        now=NOW,
        kind="scan",
    )
    assert series is not None and series.skip_reason == "spend_cap"
    assert scan is not None and scan.skip_reason == "spend_cap"
    assert calls == []
    exited = _call_grok(
        ticker="KXTRUMPACT-26OCT18-T6",
        prompt="exit",
        system="s",
        grounded=False,
        environ=env,
        post=post,
        call_log_path=tmp_path / "exit.jsonl",
        spend_path=spend,
        raw_dir=tmp_path / "raw",
        now=NOW,
        kind="exit",
    )
    assert calls == ["post"]
    assert exited is not None and exited.skip_reason != "spend_cap"
    small_calls: list[str] = []

    def small_post(url, json, headers, timeout):  # noqa: A002
        small_calls.append("post")
        text = json.dumps(
            {
                "markets": [
                    {
                        "ticker": "KXTRUMPACT",
                        "prob": "0.50",
                        "confidence": "0.80",
                        "rationale": "n",
                    }
                ],
                "sources": ["https://example.test/s"],
                "summary": "n",
            }
        )
        return _Response({"output_text": text, "citations": ["https://example.test/s"]})

    small = _call_grok(
        ticker="KXTRUMPACT",
        prompt="series",
        system="s",
        grounded=False,
        environ={XAI_API_KEY_ENV: "test-key", GROK_DAILY_USD_CAP: "0.30"},
        post=small_post,
        call_log_path=tmp_path / "small.jsonl",
        spend_path=tmp_path / "small-spend.json",
        raw_dir=tmp_path / "raw",
        now=NOW,
        kind="series",
    )
    assert small_calls == ["post"]
    assert small is not None and small.skip_reason != "spend_cap"


def test_missing_bid_does_not_invent_an_exit(tmp_path: Path) -> None:
    calls: list[dict] = []
    logged = run_paper_exit_watch(
        [{"ticker": "KXTRUMPSAY-26OCT20-TEST", "side": "yes"}],
        live=False,
        market_lookup=lambda ticker: {"yes_bid": 0},
        environ={XAI_API_KEY_ENV: "test-key"},
        post=_poster(calls),
        call_log_path=tmp_path / "calls.jsonl",
        exit_log_path=tmp_path / "exits.jsonl",
        spend_path=tmp_path / "spend.json",
        now=NOW,
    )
    assert logged == 0
    assert calls == []


def test_paid_exit_off_logs_the_mark_and_does_not_post(tmp_path: Path) -> None:
    """Two cycles both write marks. The exit cache does not skip a free mark."""
    books = {
        "KXTRUMPACT-26OCT18-T6": _empty_yes_book("KXTRUMPACT-26OCT18-T6"),
        "KXTRUTHSOCIAL-26OCT24-T240": SimpleNamespace(
            yes_ask_cents=100,
            yes_bid_cents=99,
            yes_ask_size_fp="0.00",
        ),
    }
    calls: list[dict] = []
    positions = [
        {"ticker": "KXTRUMPACT-26OCT18-T6", "side": "yes"},
        {"ticker": "KXTRUTHSOCIAL-26OCT24-T240", "side": "yes"},
    ]
    marks = tmp_path / "grok_edge_marks.jsonl"
    exits = tmp_path / "grok_edge_exits.jsonl"
    kwargs = {
        "live": False,
        "market_lookup": lambda ticker: books[ticker],
        "environ": {
            XAI_API_KEY_ENV: "test-key",
            "GROK_DAILY_USD_CAP": "3",
        },
        "post": _poster(calls),
        "call_log_path": tmp_path / "calls.jsonl",
        "exit_log_path": exits,
        "mark_log_path": marks,
        "spend_path": tmp_path / "spend.json",
        "now": NOW,
    }
    assert run_paper_exit_watch(positions, **kwargs) == 2
    assert run_paper_exit_watch(positions, **kwargs) == 2
    assert calls == []
    assert not exits.exists()
    rows = _rows(marks)
    assert len(rows) == 4
    assert [row["ticker"] for row in rows] == [
        "KXTRUMPACT-26OCT18-T6",
        "KXTRUTHSOCIAL-26OCT24-T240",
        "KXTRUMPACT-26OCT18-T6",
        "KXTRUTHSOCIAL-26OCT24-T240",
    ]
    assert all(row["paid"] is False and row["bid_cents"] == 99 for row in rows)


def test_paid_exit_off_does_not_invent_a_mark_without_a_bid(tmp_path: Path) -> None:
    calls: list[dict] = []
    marks = tmp_path / "grok_edge_marks.jsonl"
    logged = run_paper_exit_watch(
        [{"ticker": "KXTRUMPSAY-26OCT20-TEST", "side": "yes"}],
        live=False,
        market_lookup=lambda ticker: {"yes_bid": 0},
        environ={XAI_API_KEY_ENV: "test-key"},
        post=_poster(calls),
        call_log_path=tmp_path / "calls.jsonl",
        exit_log_path=tmp_path / "exits.jsonl",
        mark_log_path=marks,
        spend_path=tmp_path / "spend.json",
        now=NOW,
    )
    assert logged == 0
    assert calls == []
    assert not marks.exists()


def test_position_rows_use_the_signed_count() -> None:
    rows = paper_position_rows(
        [
            {"ticker": "KXTRUMPSAY-YES", "position_fp": "8.00"},
            {"ticker": "KXTRUTHSOCIAL-NO", "position": "-8"},
            {"ticker": "FLAT", "position_fp": "0"},
            {"market_ticker": "MISSING"},
        ]
    )
    assert rows == [
        {"ticker": "KXTRUMPSAY-YES", "side": "yes"},
        {"ticker": "KXTRUTHSOCIAL-NO", "side": "no"},
    ]


def test_scan_interval_cannot_drop_below_five_minutes() -> None:
    """180 minutes fits 8 cycles in a UTC day. Measured cycle cost stays under $3."""
    assert edge_scan_interval_seconds({}) == 10800
    assert edge_scan_interval_seconds({"EVENT_NEWS_GROK_EDGE_SCAN_MINUTES": "1"}) == 300
    assert edge_scan_interval_seconds({"EVENT_NEWS_GROK_EDGE_SCAN_MINUTES": "30"}) == 1800
    cycles = (24 * 60 * 60) // edge_scan_interval_seconds({})
    assert cycles == 8
    assert cycles * MEASURED_CYCLE_COST_USD < DEFAULT_DAILY_USD_CAP


def test_new_flags_do_not_arm_the_live_order_path_or_the_plist() -> None:
    assert EVENT_NEWS_GROK_EDGE_SCAN not in _DESK_FLAG_NAMES
    assert EVENT_NEWS_GROK_EXIT_WATCH not in _DESK_FLAG_NAMES
    assert EVENT_NEWS_GROK_KALSHI_SERIES not in _DESK_FLAG_NAMES
    assert "EVENT_NEWS_GROK_EXIT_PAID" not in _DESK_FLAG_NAMES
    assert event_news_any_live_desk_flag(
        {EVENT_NEWS_GROK_EDGE_SCAN: "true", EVENT_NEWS_GROK_EXIT_WATCH: "true"}
    ) is False
    assert event_news_grok_grounding_for_mode(True, environ={}) is False
    assert event_news_grok_grounding_for_mode(False, environ={}) is True
    for relative in (
        "scripts/start_politics_paper.sh",
        "deploy/com.jake.kalshi-bot-politics.plist",
    ):
        text = Path(relative).read_text(encoding="utf-8")
        assert "EVENT_NEWS_GROK_EDGE_SCAN" not in text
        assert "EVENT_NEWS_GROK_EXIT_WATCH" not in text
        assert "EVENT_NEWS_GROK_GROUNDING" not in text
        assert "EVENT_NEWS_GROK_KALSHI_SERIES" not in text
        assert "EVENT_NEWS_GROK_EXIT_PAID" not in text
        assert "KALSHI_GROK_PAPER_SIDECAR" not in text
        assert "GROK_DAILY_USD_CAP" not in text
    politics_script = Path("scripts/start_politics_paper.sh").read_text(encoding="utf-8")
    assert "EVENT_NEWS_UNIFIED_EDGE" not in politics_script
    assert "export POLYMARKET_US_ENABLED=false" in politics_script
    assert "export POLYMARKET_US_LIVE_TRADING_ENABLED=false" in politics_script
    assert "EVENT_NEWS_EQUITY_LOSS_HALT" not in politics_script
    assert "EVENT_NEWS_LIVE_TRADABLE_RESEARCH" not in politics_script
    assert "export EVENT_NEWS_SESSION_HALT" not in politics_script
    plist = Path("deploy/com.jake.kalshi-bot-politics.plist").read_text(encoding="utf-8")
    assert "EVENT_NEWS_UNIFIED_EDGE" not in plist
    sidecar_script = Path("scripts/start_grok_paper.sh").read_text(encoding="utf-8")
    assert 'export PYTHONPATH="$ROOT"' in sidecar_script
    assert "PYTHONPATH:+" not in sidecar_script
    assert "EVENT_NEWS_UNIFIED_EDGE" not in sidecar_script
    assert "EVENT_NEWS_EQUITY_LOSS_HALT" not in sidecar_script
    assert "EVENT_NEWS_LIVE_TRADABLE_RESEARCH" not in sidecar_script
    assert "EVENT_NEWS_SESSION_HALT" not in sidecar_script
    assert "LIVE_TRADING_ENABLED=false" in sidecar_script
    assert "export POLYMARKET_US_ENABLED=false" in sidecar_script
    assert "export POLYMARKET_US_LIVE_TRADING_ENABLED=false" in sidecar_script
    assert "KALSHI_GROK_PAPER_SIDECAR=true" in sidecar_script
    assert "EVENT_NEWS_GROK_EDGE_SCAN=true" in sidecar_script
    assert "EVENT_NEWS_GROK_EXIT_WATCH=true" in sidecar_script
    assert "EVENT_NEWS_GROK_GROUNDING=true" in sidecar_script
    assert "EVENT_NEWS_GROK_KALSHI_SERIES=false" in sidecar_script
    assert "EVENT_NEWS_GROK_EXIT_PAID" not in sidecar_script
    assert "GROK_DAILY_USD_CAP=3" in sidecar_script
    assert "bot_runtime.kalshi-grok-paper.lock" in sidecar_script
    assert "bot_runtime.kalshi-event-news-20260820.lock" not in sidecar_script
    assert "main.py" not in sidecar_script
    executor = Path("trading/executor.py").read_text(encoding="utf-8")
    edge = Path("utils/event_news_grok_edge.py").read_text(encoding="utf-8")
    assert "proposed_contracts_for_edge" not in executor
    assert "event_news_grok_edge" not in executor
    assert "place_limit_order" not in edge
    assert "trading.executor" not in edge
    scan_source = inspect.getsource(TradingBot._run_event_news_grok_edge_once)
    exit_source = inspect.getsource(TradingBot._run_event_news_grok_exit_once)
    assert 'status="open"' in scan_source
    assert "place_limit_order" not in scan_source
    assert "get_open_positions" in exit_source
    assert "cancel" not in exit_source


def _bot(monkeypatch, *, live: bool, paper: bool) -> TradingBot:
    monkeypatch.setattr(cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.setattr(cfg, "live_trading_enabled", live)
    monkeypatch.setattr(cfg, "is_paper_trading", paper)
    monkeypatch.delenv(EVENT_NEWS_GROK_EDGE_SCAN, raising=False)
    monkeypatch.delenv(EVENT_NEWS_GROK_EXIT_WATCH, raising=False)
    bot = TradingBot.__new__(TradingBot)
    return bot


def test_live_politics_process_does_not_get(monkeypatch) -> None:
    bot = _bot(monkeypatch, live=True, paper=False)
    calls: list[dict] = []
    bot.rest = type(
        "Rest",
        (),
        {
            "get_markets": lambda self, **kwargs: calls.append(kwargs) or ([], None),
            "get_open_positions": lambda self, **kwargs: calls.append(kwargs) or [],
        },
    )()
    bot._run_event_news_grok_edge_once()
    bot._run_event_news_grok_exit_once()
    assert calls == []
    assert bot._create_event_news_grok_edge_task() is None
    assert bot._create_event_news_grok_exit_task() is None


def test_paper_scan_requests_status_open(monkeypatch) -> None:
    bot = _bot(monkeypatch, live=False, paper=True)
    seen: list[dict] = []

    def get_markets(**kwargs):
        seen.append(kwargs)
        return ([], None)

    bot.rest = type("Rest", (), {"get_markets": staticmethod(get_markets)})()
    bot._run_event_news_grok_edge_once()
    assert seen
    assert {item["status"] for item in seen} == {"open"}
    assert "KXTRUMPSAY" in {item["series_ticker"] for item in seen}
    assert "KXTRUTHSOCIAL" in {item["series_ticker"] for item in seen}


@pytest.mark.asyncio
async def test_paper_mode_starts_the_loops_and_cancels_them(monkeypatch) -> None:
    bot = _bot(monkeypatch, live=False, paper=True)
    edge = bot._create_event_news_grok_edge_task()
    watch = bot._create_event_news_grok_exit_task()
    assert edge is not None and edge.get_name() == "event_news_grok_edge"
    assert watch is not None and watch.get_name() == "event_news_grok_exit"
    edge.cancel()
    watch.cancel()
    await asyncio.gather(edge, watch, return_exceptions=True)
    assert edge.cancelled()
    assert watch.cancelled()


def test_reported_cost_above_the_reserve_fills_the_cap(tmp_path: Path) -> None:
    """A reported cost that does not fit stops every later call that day."""

    def post(url, json, headers, timeout):  # noqa: A002
        grounded = "tools" in json
        body = _body(
            "0.70",
            sources=["https://example.test/source"] if grounded else [],
            grounded=grounded,
        )
        if grounded:
            body["cost_in_usd"] = "2.00"
        return _Response(body)

    first = _scan(
        tmp_path,
        [_market()],
        post,
        {XAI_API_KEY_ENV: "test-key", "GROK_DAILY_USD_CAP": "0.60"},
    )
    assert first["queued"] == 1
    spent = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert Decimal(spent["spent_usd"]) == Decimal("0.60")
    later: list[dict] = []
    second = _scan(
        tmp_path,
        [_market(ticker="KXTRUMPSAY-26OCT20-NEXT")],
        _poster(later),
        {XAI_API_KEY_ENV: "test-key", "GROK_DAILY_USD_CAP": "0.60"},
    )
    assert later == []
    assert second["queued"] == 0
    assert second["cache_hits"] == 0


def test_exit_without_a_probability_does_not_log(tmp_path: Path) -> None:
    """A bid is marked even when the old exit reply would have failed to parse."""
    calls: list[dict] = []
    marks = tmp_path / "marks.jsonl"
    logged = run_paper_exit_watch(
        [{"ticker": "KXTRUMPSAY-26OCT20-TEST", "side": "yes"}],
        live=False,
        market_lookup=lambda ticker: {"yes_bid": 60},
        environ={XAI_API_KEY_ENV: "test-key"},
        post=_poster(calls),
        call_log_path=tmp_path / "calls.jsonl",
        exit_log_path=tmp_path / "exits.jsonl",
        mark_log_path=marks,
        spend_path=tmp_path / "spend.json",
        raw_dir=tmp_path / "raw",
        now=NOW,
    )
    assert logged == 1
    assert calls == []
    assert not (tmp_path / "exits.jsonl").exists()
    assert not (tmp_path / "calls.jsonl").exists()
    assert _rows(marks)[0]["bid_cents"] == 60


def test_two_reserves_cannot_both_pass_the_same_cap(tmp_path: Path) -> None:
    path = tmp_path / "spend.json"
    results: list[Decimal | None] = []
    barrier = threading.Barrier(2)

    def once() -> None:
        barrier.wait()
        results.append(
            _reserve_spend(path, Decimal("0.50"), cap=Decimal("0.50"), now=NOW)
        )

    threads = [threading.Thread(target=once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(Decimal("0.50")) == 1
    assert results.count(None) == 1
    spent = json.loads(path.read_text(encoding="utf-8"))
    assert Decimal(spent["spent_usd"]) == Decimal("0.50")


def _seed_cache(path: Path, ticker: str, **overrides) -> None:
    row = {
        "ts": "2026-10-17T11:00:00Z",
        "prob": "0.70",
        "confidence": "0.80",
        "sources": ["https://example.test/cached"],
        "yes_ask": 55,
        "no_ask": 46,
        "post_id": "post-1",
        "series": "KXTRUMPSAY",
        "summary": "cached",
    }
    row.update(overrides)
    row.setdefault(
        "scans",
        [
            {"prob": row["prob"], "post_id": str(row.get("post_id") or "")},
            {"prob": row["prob"], "post_id": str(row.get("post_id") or "")},
        ],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tickers": {ticker: row}}) + "\n", encoding="utf-8")


def test_search_budget_defaults_to_8_and_clamps() -> None:
    """Blank, zero, and junk keep 8. The Responses tools have no result field to send."""
    assert grok_max_search_results({}) == 8
    assert grok_max_search_results({GROK_MAX_SEARCH_RESULTS: ""}) == 8
    assert grok_max_search_results({GROK_MAX_SEARCH_RESULTS: "0"}) == 8
    assert grok_max_search_results({GROK_MAX_SEARCH_RESULTS: "nope"}) == 8
    assert grok_max_search_results({GROK_MAX_SEARCH_RESULTS: "4"}) == 4
    assert grok_max_search_results({GROK_MAX_SEARCH_RESULTS: "99"}) == 20
    trump, turns = _grounded_tool_payload("KXTRUMPSAY", NOW, 8)
    assert turns == 2
    assert trump[1]["from_date"] == "2026-10-14"
    assert trump[1]["to_date"] == "2026-10-18"
    assert trump[1]["allowed_x_handles"] == ["realDonaldTrump", "POTUS", "WhiteHouse"]
    assert "max_search_results" not in trump[0]
    assert "max_search_results" not in trump[1]
    other, wide = _grounded_tool_payload("KXCANUSDEAL", NOW, 16)
    assert wide == 3
    assert other[1]["from_date"] == "2026-10-14"
    assert "allowed_x_handles" not in other[1]
    assert "allowed_x_handles" not in _grounded_tool_payload("KXFED", NOW, 8)[0][1]
    assert "allowed_x_handles" not in _grounded_tool_payload("KXFISAEXTEND", NOW, 8)[0][1]


def test_below_5c_screen_does_not_pay_for_grounding(tmp_path: Path) -> None:
    """0.56 at 55c/46c is under 5c on both sides, so the grounded call is not posted."""
    calls: list[dict] = []
    yes_edge = int((_fee("0.56", 55, PAPER_QUEUE_CONTRACTS)[2] * 100).to_integral_value(rounding=ROUND_FLOOR))
    no_edge = int((_fee("0.44", 46, PAPER_QUEUE_CONTRACTS)[2] * 100).to_integral_value(rounding=ROUND_FLOOR))
    assert yes_edge is not None and yes_edge < 5
    assert no_edge is not None and no_edge < 5
    counts = _scan(
        tmp_path,
        [_market()],
        _poster(calls, ungrounded_prob="0.56"),
        {XAI_API_KEY_ENV: "test-key"},
    )
    assert counts["queued"] == 0
    assert counts["screen_skipped"] == 1
    assert counts["grounded_calls"] == 0
    assert len(calls) == 1
    assert "tools" not in calls[0]["payload"]
    screen = _rows(tmp_path / "screen.jsonl")[0]
    assert screen["decision"] == "skip"
    assert screen["reason"] == "below_screen"
    assert screen["yes_edge_cents"] == yes_edge
    assert screen["no_edge_cents"] == no_edge
    candidate = _rows(tmp_path / "candidates.jsonl")[0]
    assert candidate["reason_code"] == "below_screen"
    assert Decimal(candidate["grok_probability"]) == Decimal("0.56")
    assert len(candidate["prompt_hash"]) == 16


def test_open_position_is_grounded_below_the_screen(tmp_path: Path) -> None:
    """An open ticker still pays for grounding when the cheap edge is under 5c."""
    calls: list[dict] = []
    ticker = "KXTRUMPSAY-26OCT20-TEST"
    counts = _scan(
        tmp_path,
        [_market()],
        _poster(calls, ungrounded_prob="0.56"),
        {XAI_API_KEY_ENV: "test-key"},
        open_tickers=(ticker,),
    )
    assert counts["queued"] == 1
    assert counts["grounded_calls"] == 1
    assert len(calls) == 2
    assert "tools" in calls[1]["payload"]
    screen = _rows(tmp_path / "screen.jsonl")[0]
    assert screen["decision"] == "ground"
    assert screen["reason"] == "open_position"
    assert screen["yes_edge_cents"] < 5
    assert screen["no_edge_cents"] < 5
    assert Decimal(_rows(tmp_path / "candidates.jsonl")[0]["grok_probability"]) == Decimal("0.70")


def test_strike_names_and_a_post_change_does_not_waive_the_gap() -> None:
    """T20 is 20 and E38.9 is 38.9. A new post id does not make 0.48 and 0.28 one probability."""
    assert _strike_text("KXHORMUZWEEKLY-26OCT18-T20") == "20"
    assert _strike_text("KXTRUMPAPPROVE-26OCT19-E38.9") == "38.9"
    assert _strike_text("KXTRUMPSAY-26OCT20-TEST") == ""
    jumped = (
        {"prob": "0.48", "post_id": "one"},
        {"prob": "0.28", "post_id": "two"},
    )
    assert stable_scan_probability(jumped) is None
    held = (
        {"prob": "0.28", "post_id": "one"},
        {"prob": "0.28", "post_id": "two"},
    )
    assert stable_scan_probability(held) == Decimal("0.28")
    three = (
        {"prob": "0.04", "post_id": "a"},
        {"prob": "0.06", "post_id": "b"},
        {"prob": "0.04", "post_id": "c"},
    )
    assert stable_scan_probability(three) == Decimal("0.04")


def test_one_grounded_scan_does_not_queue(tmp_path: Path) -> None:
    """The first grounded sample is recorded and skipped. The agreeing scan queues the median."""
    calls: list[dict] = []
    first = _scan(
        tmp_path,
        [_market()],
        _poster(calls),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert first["queued"] == 0
    assert first["grounded_calls"] == 1
    assert len(calls) == 2
    skipped = _rows(tmp_path / "candidates.jsonl")[0]
    assert skipped["skip_reason"] == "prob_unstable"
    assert Decimal(skipped["grok_probability"]) == Decimal("0.70")
    second_calls: list[dict] = []
    second = _scan(
        tmp_path,
        [_market()],
        _poster(second_calls),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert second["queued"] == 1
    assert second["grounded_calls"] == 1
    assert len(second_calls) == 2
    queued = _rows(tmp_path / "candidates.jsonl")[-1]
    assert queued["queued"] is True
    assert Decimal(queued["grok_probability"]) == Decimal("0.70")


def _scan_probs(path: Path) -> list[str]:
    stored = json.loads(path.read_text(encoding="utf-8"))
    scans = stored["tickers"]["KXTRUMPSAY-26OCT20-TEST"]["scans"]
    return [str(row["prob"]) for row in scans]


def test_wide_gap_gets_one_grounded_tie_break_then_stops(tmp_path: Path) -> None:
    """0.20 then 0.70 is not a decision. One grounded 0.40 still skips, and the next cycle is free."""
    calls: list[dict] = []
    first = _scan(
        tmp_path,
        [_market()],
        _poster(calls),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob="0.20",
    )
    assert first["queued"] == 0
    assert first["grounded_calls"] == 1
    assert len(calls) == 2
    skipped = _rows(tmp_path / "candidates.jsonl")[0]
    assert skipped["skip_reason"] == "prob_unstable"
    assert Decimal(skipped["grok_probability"]) == Decimal("0.70")
    later: list[dict] = []
    second = _scan(
        tmp_path,
        [_market()],
        _poster(later, grounded_prob="0.40"),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert len(later) == 1
    assert "tools" in later[0]["payload"]
    assert later[0]["payload"]["temperature"] == 0
    assert later[0]["payload"]["model"] == GROK_MODEL
    assert second["queued"] == 0
    assert second["cache_hits"] == 0
    assert second["grounded_calls"] == 1
    assert _rows(tmp_path / "candidates.jsonl")[-1]["skip_reason"] == "prob_unstable"
    assert _scan_probs(tmp_path / "cache.json") == ["0.20", "0.70", "0.40"]
    again: list[dict] = []
    third = _scan(
        tmp_path,
        [_market()],
        _poster(again, grounded_prob="0.90"),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert again == []
    assert third["cache_hits"] == 1
    assert third["queued"] == 0
    assert third["grounded_calls"] == 0
    assert _rows(tmp_path / "candidates.jsonl")[-1]["skip_reason"] == "prob_unstable"
    assert _scan_probs(tmp_path / "cache.json") == ["0.20", "0.70", "0.40"]


def test_tie_break_queues_the_median_of_three(tmp_path: Path) -> None:
    """0.20, 0.70, then 0.68. The last two are within 0.10, so the queue is 0.68."""
    _scan(
        tmp_path,
        [_market()],
        _poster([]),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob="0.20",
    )
    later: list[dict] = []
    second = _scan(
        tmp_path,
        [_market()],
        _poster(later, grounded_prob="0.68"),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert len(later) == 1
    assert "tools" in later[0]["payload"]
    assert second["queued"] == 1
    assert second["grounded_calls"] == 1
    queued = _rows(tmp_path / "candidates.jsonl")[-1]
    assert queued["queued"] is True
    assert Decimal(queued["grok_probability"]) == Decimal("0.68")
    assert _scan_probs(tmp_path / "cache.json") == ["0.20", "0.70", "0.68"]
    again: list[dict] = []
    third = _scan(
        tmp_path,
        [_market()],
        _poster(again),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert again == []
    assert third["cache_hits"] == 1
    assert third["queued"] == 1
    assert third["grounded_calls"] == 0


def test_tie_break_skips_when_the_cap_does_not_fit(tmp_path: Path) -> None:
    """A ledger already at $3 buys no third scan and writes no call row."""
    _scan(
        tmp_path,
        [_market()],
        _poster([]),
        {XAI_API_KEY_ENV: "test-key", GROK_DAILY_USD_CAP: "3"},
        prior_prob="0.20",
    )
    (tmp_path / "spend.json").write_text(
        json.dumps({"date": "2026-10-17", "spent_usd": "3"}) + "\n",
        encoding="utf-8",
    )
    logged = len(_rows(tmp_path / "calls.jsonl"))
    later: list[dict] = []
    second = _scan(
        tmp_path,
        [_market()],
        _poster(later, grounded_prob="0.68"),
        {XAI_API_KEY_ENV: "test-key", GROK_DAILY_USD_CAP: "3"},
        prior_prob=None,
    )
    assert later == []
    assert len(_rows(tmp_path / "calls.jsonl")) == logged
    assert second["queued"] == 0
    assert second["grounded_calls"] == 0
    assert second["cache_hits"] == 0
    assert _rows(tmp_path / "candidates.jsonl")[-1]["skip_reason"] == "prob_unstable"
    assert _scan_probs(tmp_path / "cache.json") == ["0.20", "0.70"]
    retry: list[dict] = []
    third = _scan(
        tmp_path,
        [_market()],
        _poster(retry, grounded_prob="0.68"),
        {XAI_API_KEY_ENV: "test-key", GROK_DAILY_USD_CAP: "3"},
        prior_prob=None,
    )
    assert retry == []
    assert third["grounded_calls"] == 0
    assert _scan_probs(tmp_path / "cache.json") == ["0.20", "0.70"]


def test_tie_break_fails_closed_when_the_ledger_is_unreadable(tmp_path: Path) -> None:
    """Garbage in the spend file is not a reason to buy the third scan."""
    _scan(
        tmp_path,
        [_market()],
        _poster([]),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob="0.20",
    )
    (tmp_path / "spend.json").write_text("not-json\n", encoding="utf-8")
    logged = len(_rows(tmp_path / "calls.jsonl"))
    later: list[dict] = []
    second = _scan(
        tmp_path,
        [_market()],
        _poster(later, grounded_prob="0.68"),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert later == []
    assert len(_rows(tmp_path / "calls.jsonl")) == logged
    assert second["grounded_calls"] == 0
    assert second["queued"] == 0
    assert _rows(tmp_path / "candidates.jsonl")[-1]["skip_reason"] == "prob_unstable"
    assert _scan_probs(tmp_path / "cache.json") == ["0.20", "0.70"]


def test_malformed_tie_break_does_not_consume_the_one_sample(tmp_path: Path) -> None:
    """A bad third reply leaves the two scans in place. The parser's one retry is not a new sample."""
    _scan(
        tmp_path,
        [_market()],
        _poster([]),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob="0.20",
    )

    bad: list[dict] = []

    def post(url, json, headers, timeout):  # noqa: A002
        bad.append({"payload": json})
        return _Response({"output_text": "nope"})

    failed = _scan(
        tmp_path,
        [_market()],
        post,
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert len(bad) == 2
    assert all("tools" in row["payload"] for row in bad)
    assert failed["queued"] == 0
    assert failed["grounded_calls"] == 1
    assert _scan_probs(tmp_path / "cache.json") == ["0.20", "0.70"]
    later: list[dict] = []
    second = _scan(
        tmp_path,
        [_market()],
        _poster(later, grounded_prob="0.40"),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert len(later) == 1
    assert "tools" in later[0]["payload"]
    assert second["queued"] == 0
    assert _rows(tmp_path / "candidates.jsonl")[-1]["skip_reason"] == "prob_unstable"
    assert _scan_probs(tmp_path / "cache.json") == ["0.20", "0.70", "0.40"]
    again: list[dict] = []
    third = _scan(
        tmp_path,
        [_market()],
        _poster(again),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert again == []
    assert third["cache_hits"] == 1
    assert third["grounded_calls"] == 0


def test_ask_move_or_new_post_is_a_full_rescan(tmp_path: Path) -> None:
    """A 5c ask move or a new post id pays for the screen and the grounded call."""
    moved = tmp_path / "moved"
    moved.mkdir()
    _scan(
        moved,
        [_market()],
        _poster([]),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob="0.20",
    )
    ask_calls: list[dict] = []
    _scan(
        moved,
        [_market(yes_ask=60)],
        _poster(ask_calls, grounded_prob="0.40"),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert len(ask_calls) == 2
    assert "tools" not in ask_calls[0]["payload"]
    assert "tools" in ask_calls[1]["payload"]

    posted = tmp_path / "posted"
    posted.mkdir()
    _scan(
        posted,
        [_market()],
        _poster([]),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob="0.20",
    )
    post_calls: list[dict] = []
    counts = _scan(
        posted,
        [_market()],
        _poster(post_calls, grounded_prob="0.40"),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
        post_lookup=lambda: "post-2",
    )
    assert len(post_calls) == 2
    assert "tools" not in post_calls[0]["payload"]
    assert "tools" in post_calls[1]["payload"]
    assert counts["queued"] == 0
    assert _rows(posted / "candidates.jsonl")[-1]["skip_reason"] == "prob_unstable"


def test_open_position_tie_break_is_one_call_and_the_scan_does_not_repeat(
    tmp_path: Path,
) -> None:
    """The position score spends the one tie-break. The scan behind it does not pay again."""
    _scan(
        tmp_path,
        [_market()],
        _poster([]),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob="0.20",
    )
    paths = _paths(tmp_path)
    calls: list[dict] = []
    scored = score_open_positions(
        [{"ticker": "KXTRUMPSAY-26OCT20-TEST", "position_fp": "8"}],
        live=False,
        market_lookup=lambda ticker: _market(),
        environ={XAI_API_KEY_ENV: "test-key"},
        post=_poster(calls, grounded_prob="0.68"),
        now=NOW,
        call_log_path=paths["call_log_path"],
        score_log_path=tmp_path / "scores.jsonl",
        spend_path=paths["spend_path"],
        cache_path=paths["cache_path"],
        screen_log_path=paths["screen_log_path"],
        post_lookup=lambda: "post-1",
    )
    assert len(calls) == 1
    assert "tools" in calls[0]["payload"]
    assert calls[0]["payload"]["temperature"] == 0
    assert scored["grounded_calls"] == 1
    assert scored["scored"] == 1
    score_row = _rows(tmp_path / "scores.jsonl")[-1]
    assert score_row["decision"] == "take"
    assert Decimal(score_row["grok_probability"]) == Decimal("0.68")
    assert _scan_probs(paths["cache_path"]) == ["0.20", "0.70", "0.68"]
    again: list[dict] = []
    queued = _scan(
        tmp_path,
        [_market()],
        _poster(again),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob=None,
    )
    assert again == []
    assert queued["cache_hits"] == 1
    assert queued["grounded_calls"] == 0
    assert queued["queued"] == 1
    assert Decimal(_rows(tmp_path / "candidates.jsonl")[-1]["grok_probability"]) == Decimal("0.68")


def test_two_scans_queue_their_median(tmp_path: Path) -> None:
    """0.60 and 0.70 are within 0.10, so the queued probability is 0.65."""
    calls: list[dict] = []
    counts = _scan(
        tmp_path,
        [_market()],
        _poster(calls),
        {XAI_API_KEY_ENV: "test-key"},
        prior_prob="0.60",
    )
    assert counts["queued"] == 1
    assert Decimal(_rows(tmp_path / "candidates.jsonl")[0]["grok_probability"]) == Decimal("0.65")


def test_cache_hits_do_not_consume_paid_slots(tmp_path: Path) -> None:
    """Three fresh cache rows leave the paid slots for the uncached book behind them."""
    tickers: dict[str, dict] = {}
    cached = []
    for index in range(3):
        ticker = f"KXTRUMPSAY-26OCT20-C{index}"
        cached.append(_market(ticker=ticker))
        tickers[ticker] = {
            "ts": "2026-10-17T11:00:00Z",
            "prob": "0.70",
            "confidence": "0.80",
            "sources": ["https://example.test/cached"],
            "yes_ask": 55,
            "no_ask": 46,
            "post_id": "post-1",
            "series": "KXTRUMPSAY",
            "summary": "cached",
            "scans": [
                {"prob": "0.70", "post_id": "post-1"},
                {"prob": "0.70", "post_id": "post-1"},
            ],
        }
    fresh = _market(ticker="KXTRUMPSAY-26OCT20-FRESH")
    (tmp_path / "cache.json").write_text(
        json.dumps({"tickers": tickers}) + "\n",
        encoding="utf-8",
    )
    calls: list[dict] = []
    counts = _scan(
        tmp_path,
        [*cached, fresh],
        _poster(calls, ungrounded_prob="0.56"),
        {XAI_API_KEY_ENV: "test-key"},
        max_markets=3,
    )
    assert counts["cache_hits"] == 3
    assert counts["examined"] == 1
    assert len(calls) == 1
    assert "KXTRUMPSAY-26OCT20-FRESH" in json.dumps(calls[0]["payload"])
    assert "tools" not in calls[0]["payload"]


def test_fresh_cache_queues_without_another_grok_call(tmp_path: Path) -> None:
    """A 3h grounded read is reused. The cache hit is what queues, not a new call."""
    ticker = "KXTRUMPSAY-26OCT20-TEST"
    _seed_cache(tmp_path / "cache.json", ticker)
    calls: list[dict] = []
    counts = _scan(tmp_path, [_market()], _poster(calls), {XAI_API_KEY_ENV: "test-key"})
    assert calls == []
    assert counts["cache_hits"] == 1
    assert counts["queued"] == 1
    assert counts["grounded_calls"] == 0
    screen = _rows(tmp_path / "screen.jsonl")[0]
    assert screen["decision"] == "cache_hit"
    assert screen["reason"] == "cache_fresh"
    candidate = _rows(tmp_path / "candidates.jsonl")[0]
    assert candidate["sources"] == ["https://example.test/cached"]
    assert Decimal(candidate["grok_probability"]) == Decimal("0.70")
    assert not (tmp_path / "calls.jsonl").exists()


def test_ask_move_or_new_post_bypasses_the_3h_cache(tmp_path: Path) -> None:
    """Five cents of ask movement, or a new post id, pays for a new grounded read."""
    ticker = "KXTRUMPSAY-26OCT20-TEST"
    moved = tmp_path / "moved"
    moved.mkdir()
    _seed_cache(moved / "cache.json", ticker, yes_ask=50)
    moved_calls: list[dict] = []
    moved_counts = _scan(moved, [_market()], _poster(moved_calls), {XAI_API_KEY_ENV: "test-key"})
    assert moved_counts["cache_hits"] == 0
    assert len(moved_calls) == 2
    assert "tools" in moved_calls[1]["payload"]

    posted = tmp_path / "posted"
    posted.mkdir()
    _seed_cache(posted / "cache.json", ticker, post_id="")
    posted_calls: list[dict] = []
    posted_counts = _scan(
        posted,
        [_market()],
        _poster(posted_calls),
        {XAI_API_KEY_ENV: "test-key"},
        post_lookup=lambda: "https://example.test/new-post",
    )
    assert posted_counts["cache_hits"] == 0
    assert len(posted_calls) == 2

    quiet = tmp_path / "quiet"
    quiet.mkdir()
    _seed_cache(quiet / "cache.json", ticker, post_id="post-1")
    quiet_calls: list[dict] = []
    quiet_counts = _scan(
        quiet,
        [_market()],
        _poster(quiet_calls),
        {XAI_API_KEY_ENV: "test-key"},
        post_lookup=lambda: None,
    )
    assert quiet_calls == []
    assert quiet_counts["cache_hits"] == 1
    assert quiet_counts["queued"] == 1


def test_one_grounded_call_covers_a_truth_social_series(tmp_path: Path) -> None:
    """Two count buckets share one grounded read. A flat prob is not copied onto both."""
    assert MAX_MARKETS_PER_CYCLE == 1
    first = _market(ticker="KXTRUTHSOCIAL-26OCT17-B149", series="KXTRUTHSOCIAL")
    second = _market(ticker="KXTRUTHSOCIAL-26OCT17-B169", series="KXTRUTHSOCIAL")
    calls: list[dict] = []
    dumps = json.dumps

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(json)
        if "tools" not in json:
            return _Response(_body("0.72", sources=[], grounded=False))
        text = dumps(
            {
                "latest_post_id": "ts-1",
                "summary": "three posts",
                "sources": ["https://example.test/source"],
                "markets": [
                    {
                        "ticker": "KXTRUTHSOCIAL-26OCT17-B149",
                        "prob": "0.12",
                        "confidence": "0.50",
                        "rationale": "low",
                    },
                    {
                        "ticker": "KXTRUTHSOCIAL-26OCT17-B169",
                        "prob": "0.80",
                        "confidence": "0.50",
                        "rationale": "high",
                    },
                ],
            }
        )
        return _Response({"output_text": text, "citations": ["https://example.test/source"]})

    (tmp_path / "cache.json").write_text(
        json.dumps(
            {
                "tickers": {
                    first["ticker"]: {
                        "ts": "2026-10-17T11:00:00Z",
                        "prob": "0.12",
                        "sources": ["https://example.test/prior"],
                        "yes_ask": 55,
                        "no_ask": 46,
                        "post_id": "post-1",
                        "series": "KXTRUTHSOCIAL",
                    },
                    second["ticker"]: {
                        "ts": "2026-10-17T11:00:00Z",
                        "prob": "0.80",
                        "sources": ["https://example.test/prior"],
                        "yes_ask": 55,
                        "no_ask": 46,
                        "post_id": "post-1",
                        "series": "KXTRUTHSOCIAL",
                    },
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    counts = _scan(
        tmp_path,
        [first, second],
        post,
        {XAI_API_KEY_ENV: "test-key"},
        allowlist=("KXTRUTHSOCIAL",),
        max_markets=2,
        prior_prob=None,
    )
    grounded_posts = [payload for payload in calls if "tools" in payload]
    assert len(grounded_posts) == 1
    assert counts["examined"] == 2
    assert counts["grounded_calls"] == 1
    assert counts["queued"] == 2
    by_ticker = {row["ticker"]: row for row in _rows(tmp_path / "candidates.jsonl")}
    assert Decimal(by_ticker["KXTRUTHSOCIAL-26OCT17-B149"]["grok_probability"]) == Decimal("0.12")
    assert Decimal(by_ticker["KXTRUTHSOCIAL-26OCT17-B169"]["grok_probability"]) == Decimal("0.80")
    assert by_ticker["KXTRUTHSOCIAL-26OCT17-B149"]["sources"] == ["https://example.test/source"]
    assert by_ticker["KXTRUTHSOCIAL-26OCT17-B169"]["sources"] == ["https://example.test/source"]

    flat = tmp_path / "flat"
    flat.mkdir()
    flat_calls: list[dict] = []
    flat_counts = _scan(
        flat,
        [first, second],
        _poster(flat_calls),
        {XAI_API_KEY_ENV: "test-key"},
        allowlist=("KXTRUTHSOCIAL",),
        max_markets=2,
    )
    assert flat_counts["queued"] == 0
    assert flat_counts["grounded_calls"] == 1
    assert len([payload for payload in flat_calls if "tools" in payload["payload"]]) == 2


def test_search_cap_is_in_the_prompt_and_the_call_log(tmp_path: Path) -> None:
    """The env caps the prompt and max_turns. It is not sent as a Live Search field."""
    calls: list[dict] = []
    _scan(
        tmp_path,
        [_market(series="KXCANUSDEAL", ticker="KXCANUSDEAL-26OCT20-TEST")],
        _poster(calls),
        {XAI_API_KEY_ENV: "test-key", GROK_MAX_SEARCH_RESULTS: "4"},
        allowlist=("KXCANUSDEAL",),
    )
    grounded = calls[1]["payload"]
    assert "Use at most 4 sources." in grounded["input"][0]["content"]
    assert grounded["max_turns"] == 2
    assert "allowed_x_handles" not in grounded["tools"][1]
    assert grounded["tools"][1]["from_date"] == "2026-10-14"
    logged = [row for row in _rows(tmp_path / "calls.jsonl") if row["grounded"]][-1]
    assert logged["max_search_results"] == 4
    assert logged["max_turns"] == 2

    wide = tmp_path / "wide"
    wide.mkdir()
    wide_calls: list[dict] = []
    _scan(
        wide,
        [_market()],
        _poster(wide_calls),
        {XAI_API_KEY_ENV: "test-key", GROK_MAX_SEARCH_RESULTS: "16"},
    )
    assert wide_calls[1]["payload"]["max_turns"] == 3
    wide_logged = [row for row in _rows(wide / "calls.jsonl") if row["grounded"]][-1]
    assert wide_logged["max_search_results"] == 16
    assert wide_logged["max_turns"] == 3


def test_paper_window_keeps_ten_days_and_drops_day_thirty_one() -> None:
    """The sidecar window is 30 days. The default scan window stays 72h."""
    ten_days = _market(ticker="KXTRUMPSAY-10D", close_time=_close_in(24 * 10), volume=10)
    day_31 = _market(ticker="KXTRUMPSAY-31D", close_time=_close_in(24 * 31), volume=9999)
    four_days = _market(ticker="KXTRUMPSAY-LATER", close_time=_close_in(96), volume=5000)
    ranked = rank_politics_markets(
        [ten_days, day_31, four_days],
        now=NOW,
        series=("KXTRUMPSAY",),
        close_window=PAPER_CLOSE_WINDOW,
    )
    tickers = [row["ticker"] for row in ranked]
    assert tickers == ["KXTRUMPSAY-LATER", "KXTRUMPSAY-10D"]
    assert rank_politics_markets([four_days], now=NOW, series=("KXTRUMPSAY",)) == []


def test_cache_is_one_probability_per_utc_day(tmp_path: Path) -> None:
    """Yesterday's read misses. A read from earlier today still hits at 23:00 UTC."""
    ticker = "KXTRUMPSAY-26OCT20-TEST"
    stale = tmp_path / "stale"
    stale.mkdir()
    _seed_cache(stale / "cache.json", ticker, ts="2026-10-16T23:00:00Z")
    stale_calls: list[dict] = []
    stale_counts = _scan(stale, [_market()], _poster(stale_calls), {XAI_API_KEY_ENV: "test-key"})
    assert stale_counts["cache_hits"] == 0
    assert len(stale_calls) == 2

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _seed_cache(fresh / "cache.json", ticker, ts="2026-10-17T00:30:00Z")
    fresh_calls: list[dict] = []
    later = datetime(2026, 10, 17, 23, 0, tzinfo=timezone.utc)
    fresh_counts = _scan(
        fresh,
        [_market()],
        _poster(fresh_calls),
        {XAI_API_KEY_ENV: "test-key"},
        now=later,
    )
    assert fresh_calls == []
    assert fresh_counts["cache_hits"] == 1
    assert fresh_counts["queued"] == 1


def test_three_dollar_cap_stops_before_the_post(tmp_path: Path) -> None:
    """A ledger already at $3 does not buy another call. The skip has one reason."""
    (tmp_path / "spend.json").write_text(
        json.dumps({"date": "2026-10-17", "spent_usd": "3"}) + "\n",
        encoding="utf-8",
    )
    calls: list[dict] = []
    counts = _scan(
        tmp_path,
        [_market()],
        _poster(calls),
        {XAI_API_KEY_ENV: "test-key", GROK_DAILY_USD_CAP: "3"},
    )
    assert calls == []
    assert counts["queued"] == 0
    assert counts["grounded_calls"] == 0
    decision = _rows(tmp_path / "candidates.jsonl")
    assert [row["reason_code"] for row in decision] == ["spend_cap"]
    assert decision[0]["skip_reason"] == "spend_cap"
    assert decision[0]["grok_probability"] is None
    logged = _rows(tmp_path / "calls.jsonl")
    assert logged[0]["skip_reason"] == "spend_cap"
    assert "cost_usd" in logged[0]


def test_paper_series_records_a_grounded_prob_and_cannot_post(tmp_path: Path) -> None:
    """A recorded reply on a yaml paper series queues a fee-net row and never POSTs."""
    market = _market(
        ticker="KXHORMUZWEEKLY-26OCT25-T30",
        series="KXHORMUZWEEKLY",
        yes_ask=60,
        no_ask=45,
        close_time="2026-10-25T01:00:00Z",
        volume=1000,
    )
    block = event_news_live_post_block_reason(
        SimpleNamespace(
            ticker="KXHORMUZWEEKLY-26OCT25-T30",
            series_ticker="KXHORMUZWEEKLY",
        ),
        config=SimpleNamespace(paper_cohort_id=EVENT_NEWS_COHORT_ID),
    )
    assert block == "paper_series_no_post"
    calls: list[dict] = []
    db_path = tmp_path / "grok_paper.db"
    counts = _scan(
        tmp_path,
        [market],
        _poster(calls, grounded_prob="0.80", ungrounded_prob="0.80"),
        {XAI_API_KEY_ENV: "test-key"},
        allowlist=("KXHORMUZWEEKLY",),
        db_path=db_path,
        prior_prob="0.80",
    )
    assert counts["queued"] == 1
    assert all("/portfolio/orders" not in str(call["url"]) for call in calls)
    candidate = _rows(tmp_path / "candidates.jsonl")[0]
    assert candidate["series"] == "KXHORMUZWEEKLY"
    assert candidate["ticker"] == "KXHORMUZWEEKLY-26OCT25-T30"
    assert candidate["side"] == "yes"
    assert candidate["ask_cents"] == 60
    assert Decimal(candidate["grok_probability"]) == Decimal("0.80")
    quoted = event_news_fee_net_edge(Decimal("0.80"), 60, PAPER_QUEUE_CONTRACTS)
    assert quoted is not None and quoted[1] == Decimal("0.0672")
    fee_cents, edge_cents = event_news_fee_net_cents(Decimal("0.80"), 60, PAPER_QUEUE_CONTRACTS)
    assert candidate["fee_cents"] == fee_cents == 7
    assert candidate["edge_cents"] == edge_cents == 18
    assert candidate["reason_code"] == "queue"
    assert candidate["skip_reason"] is None
    assert candidate["queued"] is True
    assert len(candidate["prompt_hash"]) == 16
    assert candidate["model"] == GROK_MODEL
    assert candidate["contracts"] == 4
    logged = [row for row in _rows(tmp_path / "calls.jsonl") if row["grounded"]][-1]
    assert "cost_usd" in logged
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT series, ticker, side, ask_cents, grok_probability, fee_cents, "
            "edge_cents, reason_code, prompt_hash, model, queued FROM paper_candidates"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "KXHORMUZWEEKLY"
    assert row[1] == "KXHORMUZWEEKLY-26OCT25-T30"
    assert row[2] == "yes"
    assert row[3] == 60
    assert row[4] == "0.80"
    assert row[5] == 7
    assert row[6] == 18
    assert row[7] == "queue"
    assert row[8] == candidate["prompt_hash"]
    assert row[9] == GROK_MODEL
    assert row[10] == 1


def test_dataclass_book_with_a_one_lot_skips_illiquid_before_grok(tmp_path: Path) -> None:
    """A non-mapping book keeps its size. A 1-lot with empty 24h fields does not call Grok."""
    market = SimpleNamespace(
        ticker="KXHORMUZWEEKLY-26OCT25-THIN",
        series_ticker="KXHORMUZWEEKLY",
        status="active",
        yes_ask=60,
        no_ask=45,
        title="Hormuz weekly",
        rules_primary="Official print",
        close_time="2026-10-25T01:00:00Z",
        yes_ask_size=Decimal("1"),
        no_ask_size=Decimal("1"),
        open_interest_fp=None,
        open_interest=500,
        volume_24h_fp=None,
        volume=5000,
    )
    calls: list[dict] = []
    counts = _scan(
        tmp_path,
        [market],
        _poster(calls),
        {XAI_API_KEY_ENV: "test-key"},
        allowlist=("KXHORMUZWEEKLY",),
        db_path=tmp_path / "grok_paper.db",
    )
    assert counts["queued"] == 0
    assert counts["examined"] == 0
    assert counts["skipped"] == 1
    assert calls == []
    candidate = _rows(tmp_path / "candidates.jsonl")[0]
    assert candidate["reason_code"] == "illiquid_top_size"
    assert candidate["grok_probability"] is None
    assert candidate["queued"] is False


def _score_row(ticker: str, series: str, *, side: str = "yes", ask: int = 50) -> dict:
    return {
        "ts": "2026-10-17T12:00:00Z",
        "series": series,
        "ticker": ticker,
        "side": side,
        "ask_cents": ask,
        "yes_ask_cents": ask,
        "grok_probability": "0.80",
        "edge_cents": 8,
        "fee_cents": 2,
        "sources": ["https://example.test/source"],
        "queued": True,
        "skip_reason": None,
        "reason_code": "queue",
        "prompt_hash": "abc123abc123abcd",
        "model": GROK_MODEL,
    }


def test_scorecard_matches_the_hand_interval_and_promotion(tmp_path: Path) -> None:
    """n=2 fee-net EV and CI match the hand values. n=15 labels stay report-only."""
    win = contract_fee_net_dollars("yes", 50, True)
    loss = contract_fee_net_dollars("yes", 50, False)
    assert win == Decimal("0.4825")
    assert loss == Decimal("-0.5175")
    stats = scorecard_interval([win, loss])
    assert stats["n"] == 2
    assert stats["mean"] == Decimal("-0.0175")
    variance = ((win - stats["mean"]) ** 2 + (loss - stats["mean"]) ** 2) / Decimal(1)
    half = Decimal("1.96") * variance.sqrt() / Decimal(2).sqrt()
    # Residuals are exactly +/- 0.5, so variance is 0.5. Decimal.sqrt leaves a 1-ulp tail.
    assert half == Decimal("0.9800000000000000000000000003")
    assert stats["ci95_low"] == Decimal("-0.9975000000000000000000000003")
    assert stats["ci95_high"] == Decimal("0.9625000000000000000000000003")
    flat_win = scorecard_interval([Decimal("0.10")] * 15)
    flat_loss = scorecard_interval([Decimal("-0.10")] * 15)
    assert flat_win["ci95_low"] == Decimal("0.10")
    assert flat_loss["ci95_high"] == Decimal("-0.10")
    assert promotion_label(
        mode="paper", effective="paper", n=15, low=flat_win["ci95_low"], high=flat_win["ci95_high"]
    ) == "eligible for live"
    assert promotion_label(
        mode="live", effective="live", n=15, low=flat_loss["ci95_low"], high=flat_loss["ci95_high"]
    ) == "recommend off"
    assert promotion_label(
        mode="paper", effective="paper", n=14, low=Decimal("0.10"), high=Decimal("0.10")
    ) == ""
    assert promotion_label(
        mode="paper", effective="live", n=15, low=Decimal("0.10"), high=Decimal("0.10")
    ) == ""

    db_path = tmp_path / "grok_paper.db"
    _insert_paper_row(db_path, "paper_candidates", _score_row("KXHORMUZWEEKLY-A", "KXHORMUZWEEKLY"))
    _insert_paper_row(db_path, "paper_candidates", _score_row("KXHORMUZWEEKLY-B", "KXHORMUZWEEKLY"))

    def lookup(ticker: str) -> dict:
        result = "yes" if ticker.endswith("-A") else "no"
        return {"status": "finalized", "result": result}

    text = write_paper_scorecard(
        db_path=db_path,
        market_lookup=lookup,
        report_path=tmp_path / "card.md",
        now=NOW,
    )
    assert "| venue | series | mode |" in text
    assert "| KXHORMUZWEEKLY | paper | 2 | 1 | 0.5 | -0.0175 | " in text
    assert "| 0.34 |" in text
    calibration = (tmp_path / "card.calibration.jsonl").read_text(encoding="utf-8")
    assert "sources" not in calibration
    logged = [json.loads(line) for line in calibration.splitlines()]
    assert {row["p"] for row in logged} == {"0.80"}
    assert {row["outcome"] for row in logged} == {0, 1}
    assert "-0.9975000000000000000000000003" in text
    assert "0.9625000000000000000000000003" in text
    assert "eligible for live" not in text
    assert "recommend off" not in text

    live_db = tmp_path / "live.db"
    for index in range(15):
        _insert_paper_row(
            live_db,
            "paper_candidates",
            _score_row(f"KXTRUMPSAY-LOSS-{index}", "KXTRUMPSAY"),
        )
    off = write_paper_scorecard(
        db_path=live_db,
        market_lookup=lambda _ticker: {"status": "settled", "result": "no"},
        report_path=tmp_path / "off.md",
        now=NOW,
    )
    assert "recommend off" in off
    assert "| KXTRUMPSAY | live | 15 | 0 |" in off

    paper_db = tmp_path / "paper.db"
    for index in range(15):
        _insert_paper_row(
            paper_db,
            "paper_candidates",
            _score_row(f"KXHORMUZWEEKLY-WIN-{index}", "KXHORMUZWEEKLY"),
        )
    eligible = write_paper_scorecard(
        db_path=paper_db,
        market_lookup=lambda _ticker: {"status": "determined", "result": "yes"},
        report_path=tmp_path / "eligible.md",
        now=NOW,
    )
    assert "eligible for live" in eligible
    empty = write_paper_scorecard(
        db_path=tmp_path / "missing.db",
        market_lookup=lookup,
        report_path=tmp_path / "empty.md",
        now=NOW,
    )
    assert "no settled fee-net rows" in empty
    assert "One settled row per ticker." in text

    dup_db = tmp_path / "dup.db"
    for index in range(15):
        row = _score_row("KXHORMUZWEEKLY-SAME", "KXHORMUZWEEKLY", ask=50)
        row["ts"] = f"2026-10-17T12:{index:02d}:00Z"
        row["ask_cents"] = 60 if index == 14 else 50
        _insert_paper_row(dup_db, "paper_candidates", row)
    dup = write_paper_scorecard(
        db_path=dup_db,
        market_lookup=lambda _ticker: {"status": "finalized", "result": "yes"},
        report_path=tmp_path / "dup.md",
        now=NOW,
    )
    assert "| KXHORMUZWEEKLY | paper | 1 | 1 |" in dup
    assert "eligible for live" not in dup
    # Latest 60c ask wins. Exact taker fee is $0.0168, so a YES settlement is $0.3832.
    assert "| 0.3832 |" in dup


def _polymarket_row(ticker: str, **overrides) -> dict:
    row = _score_row(ticker, "PMTRUMPAPPROVE")
    row["venue"] = "polymarket_us"
    row.update(overrides)
    return row


def test_polymarket_settlement_scores_on_the_official_payload_and_fee(tmp_path: Path) -> None:
    """A Polymarket 1 settlement is YES. The fee is the Polymarket taker fee.

    Doc: https://docs.polymarket.us/api-reference/markets/get-market-settlement.md
    The Kalshi lookup must not see the slug.
    """
    db_path = tmp_path / "grok_paper.db"
    _insert_paper_row(db_path, "paper_candidates", _polymarket_row("parc-example-yes"))
    kalshi_calls: list[str] = []

    def kalshi(ticker: str) -> dict:
        kalshi_calls.append(ticker)
        raise AssertionError(ticker)

    text = write_paper_scorecard(
        db_path=db_path,
        market_lookup=kalshi,
        polymarket_lookup=lambda ticker: {"slug": ticker, "settlement": 1.0},
        report_path=tmp_path / "card.md",
        now=NOW,
    )
    fee_net = contract_fee_net_dollars("yes", 50, True, "polymarket_us")
    assert fee_net is not None
    assert kalshi_calls == []
    assert "| polymarket_us | PMTRUMPAPPROVE | paper | 1 | 1 |" in text
    assert format(fee_net, "f") in text
    assert "| polymarket_us | PMTRUMPAPPROVE | parc-example-yes | 1 | yes |" in text
    stored = sqlite3.connect(db_path).execute(
        "SELECT settlement_result FROM paper_candidates"
    ).fetchone()
    assert stored == ("yes",)


def test_polymarket_404_and_unknown_venue_stay_out_of_the_ev_table(tmp_path: Path) -> None:
    """A missing settlement is not a score. An unknown venue calls neither lookup."""
    db_path = tmp_path / "grok_paper.db"
    queued = _polymarket_row("parc-missing")
    _insert_paper_row(db_path, "paper_candidates", queued)
    for slug, reason in (
        ("parc-trmpapprv-wk-2026-10-23-37pt7-37pt9", "near_certain_ask"),
        ("parc-trmpapprv-wk-2026-10-23-38pt3-38pt5", "prob_unstable"),
    ):
        _insert_paper_row(
            db_path,
            "paper_candidates",
            _polymarket_row(
                slug,
                side=None,
                ask_cents=None,
                grok_probability=None,
                queued=False,
                skip_reason=reason,
            ),
        )
    other = _score_row("OTHER-1", "KXTRUMPSAY")
    _insert_paper_row(db_path, "paper_candidates", other)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE paper_candidates SET venue = 'other' WHERE ticker = 'OTHER-1'")
    calls: list[str] = []

    def kalshi(ticker: str) -> dict:
        calls.append("kalshi:" + ticker)
        return {"status": "finalized", "result": "yes"}

    def polymarket(ticker: str) -> dict:
        calls.append("poly:" + ticker)
        raise ValueError(f"Polymarket market {ticker!r} settlement not found")

    text = write_paper_scorecard(
        db_path=db_path,
        market_lookup=kalshi,
        polymarket_lookup=polymarket,
        report_path=tmp_path / "card.md",
        now=NOW,
    )
    assert "no settled fee-net rows" in text
    assert "kalshi:" not in " ".join(calls)
    assert "poly:OTHER-1" not in calls
    assert "parc-trmpapprv-wk-2026-10-23-37pt7-37pt9" in text
    assert "parc-trmpapprv-wk-2026-10-23-38pt3-38pt5" in text
    assert "| polymarket_us | PMTRUMPAPPROVE | parc-missing | 1 | not settled |" in text
    assert "unreadable" not in text


def test_stored_polymarket_settlement_rejects_a_later_flip(tmp_path: Path) -> None:
    """The first YES/NO sticks. A later opposite number is unreadable and not scored."""
    db_path = tmp_path / "grok_paper.db"
    _insert_paper_row(db_path, "paper_candidates", _polymarket_row("parc-flip"))
    payloads = [{"slug": "parc-flip", "settlement": 0.0}]

    def lookup(_ticker: str) -> dict:
        return payloads[0]

    first = write_paper_scorecard(
        db_path=db_path,
        market_lookup=lambda _ticker: {"status": "finalized", "result": "yes"},
        polymarket_lookup=lookup,
        report_path=tmp_path / "first.md",
        now=NOW,
    )
    assert "| polymarket_us | PMTRUMPAPPROVE | parc-flip | 1 | no |" in first
    stored = sqlite3.connect(db_path).execute(
        "SELECT settlement_result FROM paper_candidates"
    ).fetchone()
    assert stored == ("no",)
    payloads[0] = {"slug": "parc-flip", "settlement": 1.0}
    second = write_paper_scorecard(
        db_path=db_path,
        market_lookup=lambda _ticker: {"status": "finalized", "result": "yes"},
        polymarket_lookup=lookup,
        report_path=tmp_path / "second.md",
        now=NOW,
    )
    assert "no settled fee-net rows" in second
    assert "| polymarket_us | PMTRUMPAPPROVE | parc-flip | 1 | unreadable |" in second
    stuck = sqlite3.connect(db_path).execute(
        "SELECT settlement_result FROM paper_candidates"
    ).fetchone()
    assert stuck == ("no",)


def test_later_blank_row_cannot_hide_or_flip_a_stored_polymarket_settlement(tmp_path: Path) -> None:
    """The earliest YES/NO wins. A newer blank row must not score the opposite live price."""
    db_path = tmp_path / "grok_paper.db"
    _insert_paper_row(db_path, "paper_candidates", _polymarket_row("parc-flip"))
    write_paper_scorecard(
        db_path=db_path,
        market_lookup=lambda _ticker: {"status": "finalized", "result": "yes"},
        polymarket_lookup=lambda _ticker: {"slug": "parc-flip", "settlement": 0.0},
        report_path=tmp_path / "first.md",
        now=NOW,
    )
    later = _polymarket_row("parc-flip", ts="2026-10-18T12:00:00Z")
    _insert_paper_row(db_path, "paper_candidates", later)
    second = write_paper_scorecard(
        db_path=db_path,
        market_lookup=lambda _ticker: {"status": "finalized", "result": "yes"},
        polymarket_lookup=lambda _ticker: {"slug": "parc-flip", "settlement": 1.0},
        report_path=tmp_path / "second.md",
        now=NOW,
    )
    assert "no settled fee-net rows" in second
    assert "| polymarket_us | PMTRUMPAPPROVE | parc-flip | 1 | unreadable |" in second
    assert "| polymarket_us | PMTRUMPAPPROVE | parc-flip | 1 | yes |" not in second
    stored = sqlite3.connect(db_path).execute(
        "SELECT ts, settlement_result FROM paper_candidates ORDER BY ts ASC, rowid ASC"
    ).fetchall()
    assert stored == [("2026-10-17T12:00:00Z", "no"), ("2026-10-18T12:00:00Z", None)]


def test_daily_report_does_not_score_a_stored_polymarket_flip(tmp_path: Path) -> None:
    """A stored NO plus a live 1 is not a hit, and the slug is not sent to Kalshi."""
    db_path = tmp_path / "grok_paper.db"
    _insert_paper_row(db_path, "paper_candidates", _polymarket_row("parc-flip"))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE paper_candidates SET settlement_result = 'no' WHERE ticker = 'parc-flip'"
        )
    (tmp_path / "candidates.jsonl").write_text(
        json.dumps(
            {
                "ticker": "parc-flip",
                "venue": "polymarket_us",
                "grok_probability": "0.70",
                "yes_ask_cents": 55,
                "side": "yes",
                "ask_cents": 55,
                "edge_cents": 13,
                "queued": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "comparisons.jsonl").write_text(
        json.dumps({"ticker": "parc-flip", "grounded_probability": "0.70", "ungrounded_probability": "0.40"})
        + "\n",
        encoding="utf-8",
    )
    kalshi_calls: list[str] = []

    def market_lookup(ticker: str) -> dict:
        kalshi_calls.append(ticker)
        return {"status": "finalized", "result": "yes"}

    text = write_daily_edge_report(
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        market_lookup=market_lookup,
        polymarket_lookup=lambda _ticker: {"slug": "parc-flip", "settlement": 1.0},
        report_path=tmp_path / "grok-edge-daily.md",
        db_path=db_path,
        now=NOW,
        decision_log_path=tmp_path / "veto-decisions.jsonl",
    )
    assert kalshi_calls == []
    assert "n: not enough data" in text
    stored = sqlite3.connect(db_path).execute(
        "SELECT settlement_result FROM paper_candidates"
    ).fetchone()
    assert stored == ("no",)


def test_profit_share_floor_blocks_paper_and_still_allows_exit(tmp_path: Path) -> None:
    """An unreadable 7-day P&L stops paper at $0.75. Exit still uses the $3 cap."""
    from utils.event_news_grok_edge import _call_grok

    spend = tmp_path / "spend.json"
    spend.write_text(
        json.dumps({"date": "2026-10-17", "spent_usd": "0.76"}) + "\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append("post")
        return _Response(
            {"output_text": json_dumps_contrary(True), "citations": ["https://example.test/contrary"]}
        )

    env = {XAI_API_KEY_ENV: "test-key", GROK_DAILY_USD_CAP: "3"}
    blocked = _call_grok(
        ticker="KXTRUMPACT",
        prompt="series",
        system="s",
        grounded=False,
        environ=env,
        post=post,
        call_log_path=tmp_path / "series.jsonl",
        spend_path=spend,
        raw_dir=tmp_path / "raw",
        now=NOW,
        kind="series",
    )
    assert calls == []
    assert blocked is not None and blocked.skip_reason == "spend_cap"
    exited = _call_grok(
        ticker="KXTRUMPACT-26OCT18-T6",
        prompt="exit",
        system="s",
        grounded=False,
        environ=env,
        post=post,
        call_log_path=tmp_path / "exit.jsonl",
        spend_path=spend,
        raw_dir=tmp_path / "raw",
        now=NOW,
        kind="exit",
    )
    assert calls == ["post"]
    assert exited is not None and exited.skip_reason != "spend_cap"


def test_profit_share_uses_a_quarter_of_the_trailing_week(tmp_path: Path) -> None:
    """$12 of settled fee-net is a $3 share, so paper still stops at $2."""
    from utils.event_news_grok_edge import _call_grok

    _seed_pnl(tmp_path, "12")
    payload = json.loads((tmp_path / "grok_settled_fee_net.json").read_text(encoding="utf-8"))
    payload["rows"].append({"close": "2026-10-10T00:00:00Z", "fee_net": "100", "status": "settled"})
    (tmp_path / "grok_settled_fee_net.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    spend = tmp_path / "spend.json"
    spend.write_text(
        json.dumps({"date": "2026-10-17", "spent_usd": "1.50"}) + "\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append("post")
        text = json.dumps(
            {
                "markets": [
                    {"ticker": "KXTRUMPACT", "prob": "0.50", "confidence": "0.80", "rationale": "n"}
                ],
                "sources": ["https://example.test/s"],
                "summary": "n",
            }
        )
        return _Response({"output_text": text, "citations": ["https://example.test/s"]})

    allowed = _call_grok(
        ticker="KXTRUMPACT",
        prompt="series",
        system="s",
        grounded=False,
        environ={XAI_API_KEY_ENV: "test-key", GROK_DAILY_USD_CAP: "3"},
        post=post,
        call_log_path=tmp_path / "series.jsonl",
        spend_path=spend,
        raw_dir=tmp_path / "raw",
        now=NOW,
        kind="series",
    )
    assert calls == ["post"]
    assert allowed is not None and allowed.skip_reason != "spend_cap"
    spend.write_text(
        json.dumps({"date": "2026-10-17", "spent_usd": "2.01"}) + "\n",
        encoding="utf-8",
    )
    blocked = _call_grok(
        ticker="KXTRUMPACT",
        prompt="series",
        system="s",
        grounded=False,
        environ={XAI_API_KEY_ENV: "test-key", GROK_DAILY_USD_CAP: "3"},
        post=post,
        call_log_path=tmp_path / "later.jsonl",
        spend_path=spend,
        raw_dir=tmp_path / "raw",
        now=NOW,
        kind="series",
    )
    assert len(calls) == 1
    assert blocked is not None and blocked.skip_reason == "spend_cap"


def test_unreadable_pnl_cache_uses_the_floor(tmp_path: Path) -> None:
    from utils.event_news_grok_edge import _trailing_fee_net

    (tmp_path / "grok_settled_fee_net.json").write_text("{", encoding="utf-8")
    assert _trailing_fee_net(tmp_path / "spend.json", NOW) is None


def test_settlement_fee_net_window_matches_revenue_minus_cost_and_fee(tmp_path: Path) -> None:
    """8 YES at 64c that lose return -5.2491. A close before the 7-day window is out."""
    total = cache_settlement_fee_net(
        [
            {
                "ticker": "KXTRUMPSAY-26OCT19-ZACH",
                "market_result": "no",
                "yes_total_cost_dollars": "5.1200",
                "no_total_cost_dollars": "0",
                "revenue": 0,
                "fee_cost": "0.1291",
                "settled_time": "2026-10-19T00:00:00Z",
                "order_id": "fill-order-1",
            },
            {
                "ticker": "KXTRUMPSAY-26OCT10-OLD",
                "market_result": "yes",
                "yes_total_cost_dollars": "1",
                "no_total_cost_dollars": "0",
                "revenue": 800,
                "fee_cost": "0",
                "settled_time": "2026-10-10T00:00:00Z",
            },
        ],
        path=tmp_path / "grok_settled_fee_net.json",
        now=datetime(2026, 10, 25, tzinfo=timezone.utc),
    )
    assert total == Decimal("-5.2491")
    stored = (tmp_path / "grok_settled_fee_net.json").read_text(encoding="utf-8")
    assert "fill-order-1" not in stored
    assert "order_id" not in stored


def test_polymarket_series_pause_until_november_3() -> None:
    """PM* paper rows stay in yaml and return on 2026-11-03 UTC."""
    resume = datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert "PMTRUMPAPPROVE" not in politics_scan_series(now=NOW)
    assert "PMUFCIASLUC" not in politics_scan_series(now=NOW)
    assert "PMTRUMPAPPROVE" in politics_scan_series(now=resume)
    assert "KXTRUMPSAY" in politics_scan_series(now=NOW)
    assert _list_block("PMTRUMPAPPROVE", NOW) == "paused"
    assert _list_block("PMTRUMPAPPROVE", resume) is None
    assert _list_block("KXTRUMPSAY", NOW) is None


def test_daily_report_warns_when_spend_exceeds_half_the_week(tmp_path: Path) -> None:
    _seed_pnl(tmp_path, "1.00")
    (tmp_path / "calls.jsonl").write_text(
        json.dumps({"ts": "2026-10-17T01:00:00Z", "cost_usd": "0.60"}) + "\n",
        encoding="utf-8",
    )
    text = write_daily_edge_report(
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        market_lookup=lambda ticker: None,
        report_path=tmp_path / "grok-edge-daily.md",
        now=NOW,
        call_log_path=tmp_path / "calls.jsonl",
        decision_log_path=tmp_path / "veto-decisions.jsonl",
    )
    assert "7d grok spend vs settled fee-net: spend 0.60 USD, fee-net 1.00 USD, ratio 0.6" in text
    assert "WARN grok spend is above half of the 7-day settled fee-net" in text
    (tmp_path / "calls.jsonl").write_text(
        json.dumps({"ts": "2026-10-17T01:00:00Z", "cost_usd": "0.40"}) + "\n",
        encoding="utf-8",
    )
    quiet = write_daily_edge_report(
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        market_lookup=lambda ticker: None,
        report_path=tmp_path / "quiet.md",
        now=NOW,
        call_log_path=tmp_path / "calls.jsonl",
        decision_log_path=tmp_path / "veto-decisions.jsonl",
    )
    assert "ratio 0.4" in quiet
    assert "WARN" not in quiet


def test_veto_table_uses_the_quadratic_fee_and_sums_only_veto_calls(tmp_path: Path) -> None:
    """A 7-lot fee is the quadratic quote, not seven times the one-lot fee."""
    from utils.event_news_research import event_news_fee_net_edge

    decisions = tmp_path / "decisions.jsonl"
    calls = tmp_path / "calls.jsonl"
    decision_rows = [
        {
            "date": "2026-10-25",
            "ticker": "KXTEST-A",
            "side": "yes",
            "ask_cents": 67,
            "contracts": 7,
            "decision": "allow",
        },
        {
            "date": "2026-10-25",
            "ticker": "KXTEST-A",
            "side": "yes",
            "ask_cents": 10,
            "contracts": 1,
            "decision": "block",
        },
        {
            "date": "2026-10-25",
            "ticker": "KXTEST-B",
            "side": "no",
            "ask_cents": 40,
            "contracts": 2,
            "decision": "block",
        },
        {
            "date": "2026-10-25",
            "ticker": "KXTEST-C",
            "side": "yes",
            "ask_cents": 55,
            "contracts": 1,
            "decision": "allow",
        },
        {
            "date": "2026-10-25",
            "ticker": "KXTEST-D",
            "side": "yes",
            "ask_cents": "60",
            "contracts": 1,
            "decision": "allow",
        },
    ]
    decisions.write_text(
        "".join(json.dumps(row) + "\n" for row in decision_rows),
        encoding="utf-8",
    )
    calls.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {"kind": "veto", "cost_usd": "0.10"},
                {"kind": "veto", "cost_usd": "0.02"},
                {"kind": "scan", "cost_usd": "9.00"},
                {"kind": "source", "cost_usd": "1.00"},
            )
        ),
        encoding="utf-8",
    )

    def lookup(ticker: str) -> dict:
        if ticker == "KXTEST-C":
            return {"status": "active", "result": "yes"}
        return {"status": "finalized", "result": "yes"}

    def payout(side: str, ask: int, contracts: int, result_yes: bool) -> Decimal:
        fee = event_news_fee_net_edge(Decimal("0.5"), ask, contracts)[1]
        price = (Decimal(ask) / Decimal(100)) * Decimal(contracts)
        won = (side == "yes") == result_yes
        if won:
            return Decimal(contracts) - price - fee
        return -price - fee

    lot_fee = event_news_fee_net_edge(Decimal("0.5"), 67, 7)[1]
    one_fee = event_news_fee_net_edge(Decimal("0.5"), 67, 1)[1]
    assert lot_fee != one_fee * 7
    allowed = payout("yes", 67, 7, True)
    blocked = payout("no", 40, 2, True)
    text = write_daily_edge_report(
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        market_lookup=lookup,
        report_path=tmp_path / "report.md",
        now=NOW,
        call_log_path=calls,
        decision_log_path=decisions,
    )
    assert f"| allowed | 1 | {format(allowed, 'f')} |" in text
    assert f"| blocked | 1 | {format(blocked, 'f')} |" in text
    assert text.count("veto-call cost:") == 1
    assert "veto-call cost: 0.12" in text
    assert "9.00" not in text.split("veto-call cost:", 1)[1]
