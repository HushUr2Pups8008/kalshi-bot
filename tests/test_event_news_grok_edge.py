"""Paper Grok edge math, spend cap, and the fail-closed queue."""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import requests

from config import cfg
from main import TradingBot
from utils.event_news_desk_flags import (
    EVENT_NEWS_GROK_EDGE_SCAN,
    EVENT_NEWS_GROK_EXIT_WATCH,
    _DESK_FLAG_NAMES,
    event_news_any_live_desk_flag,
)
from utils.event_news_grok_edge import (
    GROK_EDGE_TIMEOUT_SECONDS,
    GROUNDED_RESERVE_USD,
    PAPER_QUEUE_CONTRACTS,
    brier_report,
    comparison_summary,
    edge_scan_interval_seconds,
    ev_by_edge_bucket,
    exit_versus_hold,
    fee_net_edge_cents,
    paper_position_rows,
    paper_queue_decision,
    proposed_contracts_for_edge,
    run_paper_edge_scan,
    run_paper_exit_watch,
    taker_fee_cents,
)
from utils.event_news_grok_flags import event_news_grok_grounding_for_mode
from utils.event_news_grok_veto import GROK_MODEL, XAI_API_KEY_ENV
from utils.event_news_research import EVENT_NEWS_COHORT_ID

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
ALLOW = ("KXTRUMPSAY",)


class _Response:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def json(self) -> dict:
        return self._body


def _paths(tmp_path: Path) -> dict:
    return {
        "call_log_path": tmp_path / "calls.jsonl",
        "candidate_log_path": tmp_path / "candidates.jsonl",
        "comparison_log_path": tmp_path / "comparisons.jsonl",
        "spend_path": tmp_path / "spend.json",
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


def _scan(tmp_path: Path, markets, post, environ) -> dict:
    return run_paper_edge_scan(
        markets,
        live=False,
        allowlist=ALLOW,
        environ=environ,
        post=post,
        now=NOW,
        **_paths(tmp_path),
    )


def test_taker_fee_ceils_the_half_cent_case() -> None:
    """0.07 * 1 * 0.50 * 0.50 is 1.75 cents. Kalshi rounds that fee up to 2."""
    assert taker_fee_cents(50, 1) == 2
    assert taker_fee_cents(55, 1) == 2
    assert taker_fee_cents(True, 1) is None
    assert taker_fee_cents(0, 1) is None
    assert taker_fee_cents(100, 1) is None


def test_fee_net_edge_floors_below_the_8c_gate() -> None:
    """0.70 at a 55c ask clears 8c after the 2c fee. 0.64 does not."""
    assert fee_net_edge_cents(Decimal("0.70"), 55, 1) == 13
    assert fee_net_edge_cents(Decimal("0.64"), 55, 1) == 7
    queued, edge = paper_queue_decision(
        probability=Decimal("0.70"),
        ask_cents=55,
        side="yes",
        contracts=1,
    )
    skipped, small = paper_queue_decision(
        probability=Decimal("0.64"),
        ask_cents=55,
        side="yes",
        contracts=1,
    )
    assert queued == "queue"
    assert edge == 13
    assert skipped == "edge"
    assert small == 7


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
    assert proposed_contracts_for_edge(8) == 4
    assert proposed_contracts_for_edge(12) == 6
    assert proposed_contracts_for_edge(20) == 8
    assert proposed_contracts_for_edge(100) == 8
    assert proposed_contracts_for_edge(7) is None


def test_edge_min_env_raises_the_gate(tmp_path: Path) -> None:
    """A 9c edge queues at the default 8c minimum and skips when the env says 10."""
    calls: list[dict] = []
    environ = {XAI_API_KEY_ENV: "test-key", "EVENT_NEWS_GROK_EDGE_MIN_CENTS": "10"}
    counts = _scan(
        tmp_path,
        [_market()],
        _poster(calls, grounded_prob="0.66", ungrounded_prob="0.66"),
        environ,
    )
    assert counts["queued"] == 0
    assert counts["skipped"] == 1
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
    assert counts == {"examined": 1, "queued": 1, "skipped": 0}
    assert len(calls) == 2
    ungrounded, grounded = calls
    assert "tools" not in ungrounded["payload"]
    assert grounded["payload"]["tools"] == [{"type": "web_search"}, {"type": "x_search"}]
    assert grounded["payload"]["model"] == GROK_MODEL
    assert grounded["timeout"] == GROK_EDGE_TIMEOUT_SECONDS == 20.0
    assert ungrounded["timeout"] == 20.0
    candidate = _rows(tmp_path / "candidates.jsonl")[0]
    assert candidate["queued"] is True
    assert candidate["contracts"] == 4
    assert candidate["sizing_proposal_enabled"] is False
    assert candidate["sizing_proposal_contracts"] == 6
    assert candidate["edge_cents"] >= 8
    assert candidate["sources"] == ["https://example.test/source"]
    assert "test-key" not in (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    comparison = _rows(tmp_path / "comparisons.jsonl")[0]
    assert comparison["agree"] is True
    logged = _rows(tmp_path / "calls.jsonl")
    assert len(logged[0]["prompt_hash"]) == 16
    assert logged[0]["tools"] == []
    assert logged[1]["tools"] == ["web_search", "x_search"]


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
    assert _rows(tmp_path / "candidates.jsonl") == []
    assert _rows(tmp_path / "calls.jsonl")[1]["skip_reason"] == "malformed"


def test_grounded_reply_without_a_source_does_not_queue(tmp_path: Path) -> None:
    def post(url, json, headers, timeout):  # noqa: A002
        return _Response(_body("0.80", sources=[], grounded=False))

    counts = _scan(tmp_path, [_market()], post, {XAI_API_KEY_ENV: "test-key"})
    assert counts["queued"] == 0
    assert _rows(tmp_path / "calls.jsonl")[1]["skip_reason"] == "malformed"


def test_timeout_keeps_the_reserve_and_does_not_queue(tmp_path: Path) -> None:
    def post(url, json, headers, timeout):  # noqa: A002
        raise requests.Timeout("late")

    counts = _scan(tmp_path, [_market()], post, {XAI_API_KEY_ENV: "test-key"})
    assert counts["queued"] == 0
    spent = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))
    assert Decimal(spent["spent_usd"]) == GROUNDED_RESERVE_USD + Decimal("0.05")
    assert _rows(tmp_path / "calls.jsonl")[0]["skip_reason"] == "timeout"


def test_spend_cap_skips_before_the_post(tmp_path: Path) -> None:
    """$2.60 already spent leaves room for the $0.05 call and not the $0.50 call."""
    (tmp_path / "spend.json").write_text(
        json.dumps({"date": "2026-10-17", "spent_usd": "2.60"}) + "\n",
        encoding="utf-8",
    )
    calls: list[dict] = []
    counts = _scan(tmp_path, [_market()], _poster(calls), {XAI_API_KEY_ENV: "test-key"})
    assert len(calls) == 1
    assert "tools" not in calls[0]["payload"]
    assert counts["queued"] == 0
    grounded = _rows(tmp_path / "calls.jsonl")[1]
    assert grounded["grounded"] is True
    assert grounded["skip_reason"] == "spend_cap"


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


def test_exit_logs_a_sourced_contrary_read_and_does_not_order(tmp_path: Path) -> None:
    calls: list[dict] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(json)
        text = json_dumps_contrary(True)
        return _Response({"output_text": text, "citations": ["https://example.test/contrary"]})

    logged = run_paper_exit_watch(
        [{"ticker": "KXTRUMPSAY-26OCT20-TEST", "side": "yes"}],
        live=False,
        market_lookup=lambda ticker: {"yes_bid": 60, "no_bid": 40},
        environ={XAI_API_KEY_ENV: "test-key"},
        post=post,
        call_log_path=tmp_path / "calls.jsonl",
        exit_log_path=tmp_path / "exits.jsonl",
        spend_path=tmp_path / "spend.json",
        now=NOW,
    )
    assert logged == 1
    row = _rows(tmp_path / "exits.jsonl")[0]
    assert row["bid_cents"] == 60
    assert row["paper_only"] is True
    assert row["hold_to_settle_cents"] is None
    assert row["sources"] == ["https://example.test/contrary"]
    assert "tools" in calls[0]

    def quiet(url, json, headers, timeout):  # noqa: A002
        text = json.dumps(
            {"contrary": True, "confidence": 0.9, "probability": 0.2, "sources": []}
        )
        return _Response({"output_text": text})

    silent = run_paper_exit_watch(
        [{"ticker": "KXTRUMPSAY-26OCT20-TEST", "side": "yes"}],
        live=False,
        market_lookup=lambda ticker: {"yes_bid": 60},
        environ={XAI_API_KEY_ENV: "test-key"},
        post=quiet,
        call_log_path=tmp_path / "calls2.jsonl",
        exit_log_path=tmp_path / "exits2.jsonl",
        spend_path=tmp_path / "spend2.json",
        now=NOW,
    )
    assert silent == 0
    assert not (tmp_path / "exits2.jsonl").exists()
    compared = exit_versus_hold(
        side="yes",
        bid_cents=60,
        settlement_yes=True,
        contracts=1,
        entry_ask_cents=55,
    )
    assert compared == {"exit_cents": 1, "hold_cents": 43}
    held_loss = exit_versus_hold(
        side="yes",
        bid_cents=60,
        settlement_yes=False,
        contracts=1,
        entry_ask_cents=55,
    )
    assert held_loss == {"exit_cents": 1, "hold_cents": -57}


def json_dumps_contrary(contrary: bool) -> str:
    return json.dumps(
        {
            "contrary": contrary,
            "confidence": 0.91,
            "probability": 0.2,
            "sources": ["https://example.test/contrary"],
        }
    )


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
    assert edge_scan_interval_seconds({}) == 1800
    assert edge_scan_interval_seconds({"EVENT_NEWS_GROK_EDGE_SCAN_MINUTES": "1"}) == 300
    assert edge_scan_interval_seconds({"EVENT_NEWS_GROK_EDGE_SCAN_MINUTES": "30"}) == 1800


def test_new_flags_do_not_arm_the_live_order_path_or_the_plist() -> None:
    assert EVENT_NEWS_GROK_EDGE_SCAN not in _DESK_FLAG_NAMES
    assert EVENT_NEWS_GROK_EXIT_WATCH not in _DESK_FLAG_NAMES
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
        assert "GROK_DAILY_USD_CAP" not in text
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
