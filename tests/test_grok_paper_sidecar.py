"""The paper sidecar cannot submit orders, and its report does not invent numbers."""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kalshi.rest_client import (
    KalshiRestClient,
    KalshiSidecarOrderBlocked,
    sidecar_blocks_order_write,
)
from scripts.grok_paper_sidecar import (
    arm_sidecar_environment,
    refuse_live_trading,
    run_score_then_scan,
)
from scripts.refresh_politics_cash import balance_cents
from utils.event_news_grok_edge import (
    PAPER_DB_PATH,
    score_open_positions,
    write_daily_edge_report,
)
from utils.event_news_grok_veto import XAI_API_KEY_ENV
from utils.event_news_session_halt import refresh_stale_cash

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _market() -> dict:
    return {
        "ticker": "KXTRUMPSAY-26OCT20-TEST",
        "status": "active",
        "yes_ask": 55,
        "no_ask": 46,
        "title": "Will Trump say the word",
        "rules_primary": "Official transcript",
        "close_time": "2026-10-20T23:00:00Z",
        "series_ticker": "KXTRUMPSAY",
    }


class _Response:
    status_code = 200

    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body


def test_sidecar_post_and_delete_raise_before_http(monkeypatch) -> None:
    """Order writes raise in sidecar mode and never reach the HTTP session."""
    monkeypatch.setenv("KALSHI_GROK_PAPER_SIDECAR", "true")
    client = KalshiRestClient.__new__(KalshiRestClient)
    with pytest.raises(KalshiSidecarOrderBlocked):
        client._request("POST", "/portfolio/orders", payload={"ticker": "NOPE"})
    with pytest.raises(KalshiSidecarOrderBlocked):
        client._request("DELETE", "/portfolio/orders/order-1")
    with pytest.raises(KalshiSidecarOrderBlocked):
        client.place_limit_order("KXTRUMPSAY-26OCT20-TEST", "yes", 1, 40)
    with pytest.raises(KalshiSidecarOrderBlocked):
        client.cancel_order("order-1")


def test_sidecar_off_does_not_block_the_politics_order_path(monkeypatch) -> None:
    monkeypatch.delenv("KALSHI_GROK_PAPER_SIDECAR", raising=False)
    assert sidecar_blocks_order_write("POST", "/portfolio/orders") is False
    assert sidecar_blocks_order_write("POST", "/portfolio/events/orders") is False
    assert sidecar_blocks_order_write("DELETE", "/portfolio/orders/order-1") is False
    client = KalshiRestClient.__new__(KalshiRestClient)
    with pytest.raises(Exception) as caught:
        client._request("POST", "/portfolio/events/orders", payload={"ticker": "NOPE"})
    assert not isinstance(caught.value, KalshiSidecarOrderBlocked)


def test_sidecar_still_allows_market_and_position_gets(monkeypatch) -> None:
    monkeypatch.setenv("KALSHI_GROK_PAPER_SIDECAR", "true")
    assert sidecar_blocks_order_write("GET", "/markets") is False
    assert sidecar_blocks_order_write("GET", "/markets/KXTRUMPSAY-26OCT20-TEST") is False
    assert sidecar_blocks_order_write("GET", "/portfolio/positions") is False
    assert sidecar_blocks_order_write("GET", "/portfolio/balance") is False


def test_sidecar_source_does_not_construct_an_order_client() -> None:
    source = (ROOT / "scripts" / "grok_paper_sidecar.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert "trading.executor" not in imported
    assert "place_limit_order" not in source
    assert "TradeExecutor" not in source


def test_sidecar_refuses_a_live_start(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(SystemExit) as caught:
        refuse_live_trading()
    assert caught.value.code == 2


def test_arm_keeps_an_explicit_dry_run_cap(monkeypatch) -> None:
    import os

    for name in (
        "GROK_DAILY_USD_CAP",
        "LIVE_TRADING_ENABLED",
        "KALSHI_GROK_PAPER_SIDECAR",
        "PAPER_COHORT_ID",
        "EVENT_NEWS_GROK_EDGE_SCAN",
        "EVENT_NEWS_GROK_EXIT_WATCH",
        "EVENT_NEWS_GROK_GROUNDING",
        "EVENT_NEWS_GROK_KALSHI_SERIES",
        "EVENT_NEWS_GROK_EXIT_PAID",
        "POLYMARKET_US_ENABLED",
        "POLYMARKET_US_LIVE_TRADING_ENABLED",
    ):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setenv("GROK_DAILY_USD_CAP", "0.50")
    arm_sidecar_environment()
    assert os.environ["GROK_DAILY_USD_CAP"] == "0.50"
    assert os.environ["KALSHI_GROK_PAPER_SIDECAR"] == "true"
    assert os.environ["LIVE_TRADING_ENABLED"] == "false"
    assert os.environ["EVENT_NEWS_GROK_KALSHI_SERIES"] == "false"
    assert os.environ["EVENT_NEWS_GROK_EXIT_PAID"] == "false"


def test_sidecar_logs_polymarket_paper_active_and_blocks_live(monkeypatch, capsys) -> None:
    """Politics keeps the blend flag false. This process still runs the public scan."""
    import os

    for name in (
        "GROK_DAILY_USD_CAP",
        "LIVE_TRADING_ENABLED",
        "KALSHI_GROK_PAPER_SIDECAR",
        "PAPER_COHORT_ID",
        "EVENT_NEWS_GROK_EDGE_SCAN",
        "EVENT_NEWS_GROK_EXIT_WATCH",
        "EVENT_NEWS_GROK_GROUNDING",
        "EVENT_NEWS_GROK_KALSHI_SERIES",
        "EVENT_NEWS_GROK_EXIT_PAID",
        "POLYMARKET_US_ENABLED",
        "POLYMARKET_US_LIVE_TRADING_ENABLED",
    ):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setenv("POLYMARKET_US_ENABLED", "true")
    monkeypatch.setenv("POLYMARKET_US_LIVE_TRADING_ENABLED", "true")
    arm_sidecar_environment()
    assert os.environ["POLYMARKET_US_ENABLED"] == "false"
    assert os.environ["POLYMARKET_US_LIVE_TRADING_ENABLED"] == "false"
    assert os.environ["LIVE_TRADING_ENABLED"] == "false"
    assert os.environ["EVENT_NEWS_GROK_KALSHI_SERIES"] == "false"
    assert os.environ["EVENT_NEWS_GROK_EXIT_PAID"] == "false"
    assert capsys.readouterr().err.strip() == (
        "[POLYMARKET_PAPER] active venue=polymarket_us order_endpoint=blocked"
    )


def test_reserve_floor_screen_spends_the_only_nickel(tmp_path: Path) -> None:
    """A $0.05 cap pays the ungrounded screen. The grounded reserve does not fit.

    The poster reports no usage, so the nickel stays spent. Fail closed:
    an ungrounded probability does not queue.
    """
    from utils.event_news_grok_edge import run_paper_edge_scan

    calls: list[dict] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(json)
        text = __import__("json").dumps(
            {
                "probability": "0.70",
                "confidence": "0.80",
                "verdict": "YES",
                "sources": ["https://example.test/source"],
            }
        )
        return _Response({"output_text": text, "citations": ["https://example.test/source"]})

    counts = run_paper_edge_scan(
        [_market()],
        live=False,
        allowlist=("KXTRUMPSAY",),
        environ={XAI_API_KEY_ENV: "test-key", "GROK_DAILY_USD_CAP": "0.05"},
        post=post,
        now=NOW,
        post_lookup=lambda: "post-1",
        call_log_path=tmp_path / "calls.jsonl",
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        cache_path=tmp_path / "cache.json",
        screen_log_path=tmp_path / "screen.jsonl",
        db_path=tmp_path / "grok_paper.db",
    )
    assert len(calls) == 1
    assert "tools" not in calls[0]
    assert counts["queued"] == 0
    assert counts["grounded_calls"] == 1
    logged = [
        json.loads(line)
        for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert logged[-1]["grounded"] is True
    assert logged[-1]["skip_reason"] == "spend_cap"
    assert not (tmp_path / "comparisons.jsonl").exists()


def test_kalshi_series_off_spends_nothing_and_keeps_the_maker_bid(tmp_path: Path) -> None:
    """The same nickel screen posts when the flag is unset. Off spends nothing.

    The ungrounded scan is cut with the series and tie-break calls. The
    paper ledger acceptance is $0, and that scan is a paid call. The maker
    bid stays on the skip row. Polymarket is not in this list.
    """
    from utils.event_news_grok_edge import run_paper_edge_scan

    calls: list[dict] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(json)
        return _Response({})

    run_paper_edge_scan(
        [_market()],
        live=False,
        allowlist=("KXTRUMPSAY",),
        environ={
            XAI_API_KEY_ENV: "test-key",
            "GROK_DAILY_USD_CAP": "3",
            "EVENT_NEWS_GROK_KALSHI_SERIES": "false",
        },
        post=post,
        now=NOW,
        post_lookup=lambda: "post-1",
        call_log_path=tmp_path / "calls.jsonl",
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        cache_path=tmp_path / "cache.json",
        screen_log_path=tmp_path / "screen.jsonl",
        db_path=tmp_path / "grok_paper.db",
    )
    assert calls == []
    rows = [
        json.loads(line)
        for line in (tmp_path / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["skip_reason"] == "kalshi_series_off"
    assert rows[-1]["maker_limit_cents"] == 54
    assert not (tmp_path / "calls.jsonl").exists()


def test_polymarket_scan_still_posts_when_kalshi_series_is_off(tmp_path: Path) -> None:
    """PM pause ends 2026-11-03 UTC. A Kalshi skip counts toward the cycle cap."""
    from utils.event_news_grok_edge import run_paper_edge_scan

    calls: list[dict] = []
    kalshi = _market()
    polymarket = {
        "ticker": "PMTRUMPAPPROVE-26NOV06-TEST",
        "series": "PMTRUMPAPPROVE",
        "venue": "polymarket_us",
        "status": "active",
        "yes_ask": 60,
        "no_ask": 46,
        "title": "Will Trump approve",
        "rules": "Official record",
        "close_time": "2026-11-06T23:00:00Z",
    }

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(json)
        text = __import__("json").dumps(
            {
                "probability": "0.40",
                "confidence": "0.40",
                "verdict": "NO",
                "sources": [],
            }
        )
        return _Response({"output_text": text})

    run_paper_edge_scan(
        [kalshi, polymarket],
        live=False,
        allowlist=("KXTRUMPSAY", "PMTRUMPAPPROVE"),
        max_markets=2,
        environ={
            XAI_API_KEY_ENV: "test-key",
            "GROK_DAILY_USD_CAP": "3",
            "EVENT_NEWS_GROK_KALSHI_SERIES": "false",
        },
        post=post,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        post_lookup=lambda: "post-1",
        call_log_path=tmp_path / "calls.jsonl",
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        cache_path=tmp_path / "cache.json",
        screen_log_path=tmp_path / "screen.jsonl",
        db_path=tmp_path / "grok_paper.db",
    )
    assert calls
    blob = json.dumps(calls)
    assert kalshi["ticker"] not in blob
    assert any("PMTRUMPAPPROVE-26NOV06-TEST" in json.dumps(payload) for payload in calls)


def test_exit_cycle_log_is_silent_while_paid_exit_stays_on(capsys, monkeypatch) -> None:
    import scripts.grok_paper_sidecar as sidecar

    monkeypatch.delenv("EVENT_NEWS_GROK_EXIT_PAID", raising=False)
    sidecar._log_exit_cycle(None)
    assert capsys.readouterr().err == ""
    monkeypatch.setenv("EVENT_NEWS_GROK_EXIT_PAID", "false")
    sidecar._log_exit_cycle(2)
    assert capsys.readouterr().err.strip() == "grok paper exit logged=2 paid_calls=0"
    sidecar._log_exit_cycle("nope")
    assert capsys.readouterr().err.strip() == "grok paper exit logged=0 paid_calls=0"


def test_scan_cycle_log_names_the_series_flag(capsys, monkeypatch) -> None:
    import scripts.grok_paper_sidecar as sidecar

    monkeypatch.setenv("EVENT_NEWS_GROK_KALSHI_SERIES", "false")
    sidecar._log_scan_cycle({"grounded_calls": 1}, {"grounded_calls": 2})
    assert capsys.readouterr().err.strip() == (
        "grok paper scan kalshi_series=false grounded_calls=3"
    )


def test_exit_watch_waits_out_the_sixty_seconds_before_the_long_sleep(monkeypatch) -> None:
    """A scan that finishes early still runs the exit watch at the 60-second mark."""
    import kalshi.rest_client as rest_client
    import scripts.grok_paper_sidecar as sidecar

    order: list[object] = []
    clock = {"t": 1000.0}

    def sleep(seconds: float) -> None:
        order.append(("sleep", seconds))
        clock["t"] += seconds
        if seconds >= 300:
            raise SystemExit(0)

    monkeypatch.setattr(sidecar.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(sidecar.time, "sleep", sleep)
    monkeypatch.setattr(sidecar, "refuse_live_trading", lambda: None)
    monkeypatch.setattr(sidecar, "arm_sidecar_environment", lambda: None)
    monkeypatch.setattr(
        sidecar,
        "_acquire_lock",
        lambda path: type("Lock", (), {"close": lambda self: None})(),
    )
    monkeypatch.setattr(
        sidecar,
        "run_score_then_scan",
        lambda rest, **kwargs: order.append("scan") or ({}, {}, None),
    )
    monkeypatch.setattr(sidecar, "_run_exit", lambda rest: order.append("exit"))
    monkeypatch.setattr(sidecar, "_run_daily", lambda rest: order.append("daily"))
    monkeypatch.setattr(rest_client, "KalshiRestClient", lambda: object())
    with pytest.raises(SystemExit):
        sidecar.main([])
    sleeps = [item for item in order if isinstance(item, tuple)]
    assert order[0] == "scan"
    assert sleeps[0] == ("sleep", 60)
    assert order.index("exit") < order.index(sleeps[1])
    assert sleeps[1][1] > 60


def test_wait_until_sleeps_only_the_remainder() -> None:
    import scripts.grok_paper_sidecar as sidecar

    slept: list[float] = []
    assert sidecar._wait_until(10, sleep=slept.append, clock=lambda: 10) == 10
    assert slept == []
    assert sidecar._wait_until(10, sleep=slept.append, clock=lambda: 4) == 4
    assert slept == [6]


def test_sidecar_source_does_not_use_the_prewarm_fallback() -> None:
    source = (ROOT / "scripts" / "grok_paper_sidecar.py").read_text(encoding="utf-8")
    assert "event_news_matcher_reserve_series" not in source
    assert "run_score_then_scan" in source


def test_cycle_scores_open_positions_before_the_politics_scan(monkeypatch) -> None:
    """KXDJI and a series outside the yaml are not fetched. Score runs before scan."""
    from utils.event_news_grok_edge import SCAN_TOP_N

    order: list[str] = []
    seen: dict = {}

    def score(positions, **_kwargs):
        order.append("score")
        seen["positions"] = [row["ticker"] for row in positions]
        return {"scored": 1, "skipped": 0}

    def scan(markets, **kwargs):
        order.append("scan")
        seen["scan"] = [row["ticker"] for row in markets]
        seen["max_markets"] = kwargs["max_markets"]
        seen["allow"] = set(kwargs["allowlist"])
        seen["open_tickers"] = kwargs["open_tickers"]
        return {"examined": len(markets), "queued": 0, "skipped": 0}

    monkeypatch.setattr("utils.event_news_grok_edge.score_open_positions", score)
    monkeypatch.setattr("utils.event_news_grok_edge.run_paper_edge_scan", scan)

    class Rest:
        def __init__(self) -> None:
            self.series: list[str] = []

        def get_open_positions(self, strict=True):
            assert strict is True
            return [
                {"ticker": "KXTRUMPSAY-26OCT19-ZACH", "position_fp": "8"},
                {"ticker": "KXNEWPOL-26OCT18-A", "position_fp": "-2"},
                {"ticker": "KXDJI-26OCT19-51550", "position_fp": "1"},
            ]

        def get_market(self, ticker):
            return {"ticker": ticker, "status": "active", "yes_ask": 55, "no_ask": 46}

        def get_markets(self, status="open", series_ticker="", limit=20):
            assert status == "open"
            assert limit == 20
            self.series.append(series_ticker)
            close = "2026-10-18T12:00:00Z"
            good = {
                "ticker": f"{series_ticker}-26OCT18-A",
                "series_ticker": series_ticker,
                "status": "active",
                "yes_ask": 55,
                "no_ask": 46,
                "close_time": close,
                "volume": 100 if series_ticker == "KXTRUMPSAY" else 80 if series_ticker == "KXNEWPOL" else 1,
                "title": series_ticker,
                "rules": "rules",
            }
            if series_ticker != "KXTRUMPSAY":
                return [good], None
            return [
                good,
                {**good, "ticker": "KXTRUMPSAY-OPEN", "status": "open", "volume": 500},
                {
                    **good,
                    "ticker": "KXDJI-26OCT19-51550",
                    "series_ticker": "KXDJI",
                    "series": "KXDJI",
                    "volume": 9999,
                },
                {**good, "ticker": "KXTRUMPSAY-WIDE", "yes_ask": 95, "no_ask": 6, "volume": 400},
            ], None

    rest = Rest()
    scores, scan_counts, score_error = run_score_then_scan(rest, now=NOW)
    assert score_error is None
    assert scores == {"scored": 1, "skipped": 0}
    assert order == ["score", "scan"]
    assert seen["positions"][0] == "KXTRUMPSAY-26OCT19-ZACH"
    assert "KXDJI" not in rest.series
    assert "KXNEWPOL" not in rest.series
    assert "KXTRUMPSAY" in rest.series
    assert "KXDJI" not in seen["allow"]
    assert "KXNEWPOL" not in seen["allow"]
    assert seen["max_markets"] == SCAN_TOP_N == 3
    assert seen["open_tickers"] == (
        "KXTRUMPSAY-26OCT19-ZACH",
        "KXNEWPOL-26OCT18-A",
        "KXDJI-26OCT19-51550",
    )
    assert seen["scan"][0] == "KXTRUMPSAY-26OCT18-A"
    assert len(seen["scan"]) > SCAN_TOP_N
    assert all(not ticker.startswith("KXDJI") for ticker in seen["scan"])
    assert all(not ticker.startswith("KXNEWPOL") for ticker in seen["scan"])
    assert scan_counts["fetched"] == len(rest.series) + 3
    assert scan_counts["politics"] == len(rest.series)
    assert scan_counts["examined"] == len(seen["scan"])


def test_score_failure_still_scans(monkeypatch) -> None:
    order: list[str] = []

    def score(_positions, **_kwargs):
        order.append("score")
        raise RuntimeError("score broke")

    def scan(_markets, **_kwargs):
        order.append("scan")
        return {"examined": 0, "queued": 0, "skipped": 0}

    monkeypatch.setattr("utils.event_news_grok_edge.score_open_positions", score)
    monkeypatch.setattr("utils.event_news_grok_edge.run_paper_edge_scan", scan)

    class Rest:
        def get_open_positions(self, strict=True):
            return [{"ticker": "KXTRUMPSAY-26OCT19-ZACH", "position_fp": "8"}]

        def get_market(self, ticker):
            return {"ticker": ticker}

        def get_markets(self, status="open", series_ticker="", limit=20):
            return [], None

    _scores, scan_counts, score_error = run_score_then_scan(Rest(), now=NOW)
    assert isinstance(score_error, RuntimeError)
    assert order == ["score", "scan"]
    assert scan_counts["politics"] == 0
    assert scan_counts["fetched"] == 0


def test_position_score_records_take_and_veto_without_ordering(tmp_path: Path) -> None:
    calls: list[dict] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(json["input"][1]["content"])
        if "tools" in json:
            text = __import__("json").dumps(
                {
                    "latest_post_id": "",
                    "summary": "shared",
                    "sources": ["https://example.test/source"],
                    "markets": [
                        {
                            "ticker": "KXTRUMPSAY-TAKE",
                            "prob": "0.90",
                            "confidence": "0.80",
                            "rationale": "yes",
                        },
                        {
                            "ticker": "KXTRUMPSAY-VETO",
                            "prob": "0.40",
                            "confidence": "0.80",
                            "rationale": "no",
                        },
                    ],
                }
            )
        else:
            text = __import__("json").dumps(
                {
                    "probability": "0.50",
                    "confidence": "0.50",
                    "verdict": "YES",
                    "sources": [],
                }
            )
        return _Response({"output_text": text, "citations": ["https://example.test/source"]})

    counts = score_open_positions(
        [
            {"ticker": "KXTRUMPSAY-TAKE", "position_fp": "8"},
            {"ticker": "KXTRUMPSAY-VETO", "position_fp": "8"},
        ],
        live=False,
        market_lookup=lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXTRUMPSAY",
            "status": "active",
            "title": ticker,
            "yes_ask": 55,
            "no_ask": 46,
        },
        environ={XAI_API_KEY_ENV: "test-key", "GROK_DAILY_USD_CAP": "3"},
        post=post,
        now=NOW,
        call_log_path=tmp_path / "calls.jsonl",
        score_log_path=tmp_path / "scores.jsonl",
        spend_path=tmp_path / "spend.json",
        cache_path=tmp_path / "cache.json",
        screen_log_path=tmp_path / "screen.jsonl",
        db_path=tmp_path / "grok_paper.db",
        post_lookup=lambda: "post-1",
    )
    assert counts["scored"] == 2
    assert counts["skipped"] == 0
    assert counts["cache_hits"] == 0
    assert counts["grounded_calls"] == 1
    rows = [
        json.loads(line)
        for line in (tmp_path / "scores.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    decisions = {row["ticker"]: row["decision"] for row in rows}
    assert decisions == {"KXTRUMPSAY-TAKE": "take", "KXTRUMPSAY-VETO": "veto"}
    call_rows = [
        json.loads(line)
        for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    grounded_rows = [row for row in call_rows if row["grounded"]]
    assert len(grounded_rows) == 1
    assert all(call_has_tools(row) for row in grounded_rows)
    assert any(row["grounded"] is False for row in call_rows)


def call_has_tools(row: dict) -> bool:
    return row["grounded"] is True and row["tools"] == ["web_search", "x_search"]


def test_live_position_score_does_not_call(tmp_path: Path) -> None:
    calls: list[dict] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(json)
        return _Response({})

    counts = score_open_positions(
        [{"ticker": "KXTRUMPSAY-TAKE", "position": 8}],
        live=True,
        market_lookup=lambda ticker: _market(),
        environ={XAI_API_KEY_ENV: "test-key", "EVENT_NEWS_GROK_EDGE_SCAN": "true"},
        post=post,
        now=NOW,
        call_log_path=tmp_path / "calls.jsonl",
        score_log_path=tmp_path / "scores.jsonl",
        spend_path=tmp_path / "spend.json",
    )
    assert counts == {"scored": 0, "skipped": 0}
    assert calls == []


def test_kalshi_position_score_writes_series_off_and_does_not_call(tmp_path: Path) -> None:
    calls: list[dict] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(json)
        return _Response({})

    counts = score_open_positions(
        [{"ticker": "KXTRUMPSAY-26OCT20-TEST", "position": 8}],
        live=False,
        market_lookup=lambda ticker: _market(),
        environ={
            XAI_API_KEY_ENV: "test-key",
            "GROK_DAILY_USD_CAP": "3",
            "EVENT_NEWS_GROK_KALSHI_SERIES": "false",
        },
        post=post,
        now=NOW,
        call_log_path=tmp_path / "calls.jsonl",
        score_log_path=tmp_path / "scores.jsonl",
        spend_path=tmp_path / "spend.json",
        cache_path=tmp_path / "cache.json",
        db_path=tmp_path / "grok_paper.db",
    )
    assert calls == []
    assert counts["scored"] == 0
    assert counts["skipped"] == 1
    rows = [
        json.loads(line)
        for line in (tmp_path / "scores.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["skip_reason"] == "kalshi_series_off"
    assert rows[0]["decision"] == "not_scored"


def test_polymarket_position_score_still_calls_when_kalshi_series_is_off(tmp_path: Path) -> None:
    calls: list[dict] = []

    def post(url, json, headers, timeout):  # noqa: A002
        calls.append(json)
        text = __import__("json").dumps(
            {
                "probability": "0.40",
                "confidence": "0.40",
                "verdict": "NO",
                "sources": [],
            }
        )
        return _Response({"output_text": text})

    market = {
        "ticker": "PMTRUMPAPPROVE-26NOV06-TEST",
        "series": "PMTRUMPAPPROVE",
        "venue": "polymarket_us",
        "status": "active",
        "yes_ask": 60,
        "no_ask": 46,
        "title": "Will Trump approve",
        "rules": "Official record",
        "close_time": "2026-11-06T23:00:00Z",
    }
    score_open_positions(
        [{"ticker": "PMTRUMPAPPROVE-26NOV06-TEST", "position": 4}],
        live=False,
        market_lookup=lambda ticker: market,
        environ={
            XAI_API_KEY_ENV: "test-key",
            "GROK_DAILY_USD_CAP": "3",
            "EVENT_NEWS_GROK_KALSHI_SERIES": "false",
        },
        post=post,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        call_log_path=tmp_path / "calls.jsonl",
        score_log_path=tmp_path / "scores.jsonl",
        spend_path=tmp_path / "spend.json",
        cache_path=tmp_path / "cache.json",
        db_path=tmp_path / "grok_paper.db",
        post_lookup=lambda: "post-1",
    )
    assert calls
    assert "PMTRUMPAPPROVE-26NOV06-TEST" in json.dumps(calls)


def test_empty_daily_report_does_not_invent_numbers(tmp_path: Path) -> None:
    text = write_daily_edge_report(
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        market_lookup=lambda ticker: None,
        report_path=tmp_path / "grok-edge-daily.md",
        now=NOW,
    )
    assert "not enough data" in text
    assert "hit rate: not enough data" in text
    assert "Brier vs ask: not enough data" in text
    assert "fee-net paper EV: not enough data" in text
    assert "spend: not measured" in text
    assert "100%" not in text


def test_daily_report_joins_a_settled_market_and_withholds_a_one_row_interval(tmp_path: Path) -> None:
    candidate = {
        "ts": "2026-10-17T12:00:00Z",
        "ticker": "KXTRUMPSAY-26OCT20-TEST",
        "grok_probability": "0.70",
        "yes_ask_cents": 55,
        "side": "yes",
        "ask_cents": 55,
        "edge_cents": 13,
        "queued": True,
        "contracts": 4,
        "sources": ["https://example.test/source"],
    }
    comparison = {
        "ticker": "KXTRUMPSAY-26OCT20-TEST",
        "grounded_probability": "0.70",
        "ungrounded_probability": "0.40",
        "outcome": None,
    }
    (tmp_path / "candidates.jsonl").write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    (tmp_path / "comparisons.jsonl").write_text(json.dumps(comparison) + "\n", encoding="utf-8")
    (tmp_path / "spend.json").write_text(
        json.dumps({"date": "2026-10-17", "spent_usd": "0.55"}) + "\n",
        encoding="utf-8",
    )

    def lookup(ticker: str) -> dict:
        return {"ticker": ticker, "status": "finalized", "result": "yes"}

    text = write_daily_edge_report(
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        market_lookup=lookup,
        report_path=tmp_path / "grok-edge-daily.md",
        now=NOW,
    )
    assert "n: 1" in text
    assert "hit rate: 1 of 1" in text
    assert "Brier vs ask: grok " in text
    assert "ask " in text
    assert "grounded closer than ungrounded: 1 of 1" in text
    assert "fee-net paper EV: not enough data" in text
    assert "spend: 0.55 USD on 2026-10-17" in text


def test_daily_report_settles_polymarket_on_its_endpoint(tmp_path: Path) -> None:
    """Kalshi GET is only for a Kalshi row. Unknown venues are not settled."""
    rows = [
        {
            "ticker": "KXTRUMPSAY-A",
            "grok_probability": "0.70",
            "yes_ask_cents": 55,
            "side": "yes",
            "ask_cents": 55,
            "edge_cents": 13,
            "queued": True,
        },
        {
            "ticker": "parc-example-yes",
            "venue": "polymarket_us",
            "grok_probability": "0.70",
            "yes_ask_cents": 55,
            "side": "yes",
            "ask_cents": 55,
            "edge_cents": 13,
            "queued": True,
        },
        {
            "ticker": "parc-missing",
            "venue": "polymarket_us",
            "grok_probability": "0.70",
            "yes_ask_cents": 55,
            "side": "yes",
            "ask_cents": 55,
            "edge_cents": 13,
            "queued": True,
        },
        {
            "ticker": "OTHER-1",
            "venue": "other",
            "grok_probability": "0.70",
            "yes_ask_cents": 55,
            "side": "yes",
            "ask_cents": 55,
            "edge_cents": 13,
            "queued": True,
        },
    ]
    (tmp_path / "candidates.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    (tmp_path / "comparisons.jsonl").write_text(
        json.dumps(
            {
                "ticker": "parc-missing",
                "grounded_probability": "0.70",
                "ungrounded_probability": "0.40",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    kalshi_calls: list[str] = []
    poly_calls: list[str] = []

    def market_lookup(ticker: str) -> dict:
        kalshi_calls.append(ticker)
        return {"status": "finalized", "result": "yes"}

    def polymarket_lookup(ticker: str) -> dict:
        poly_calls.append(ticker)
        if ticker == "parc-missing":
            raise ValueError(f"Polymarket market {ticker!r} settlement not found")
        return {"slug": ticker, "settlement": 1.0}

    text = write_daily_edge_report(
        candidate_log_path=tmp_path / "candidates.jsonl",
        comparison_log_path=tmp_path / "comparisons.jsonl",
        spend_path=tmp_path / "spend.json",
        market_lookup=market_lookup,
        polymarket_lookup=polymarket_lookup,
        report_path=tmp_path / "grok-edge-daily.md",
        now=NOW,
    )
    assert kalshi_calls == ["KXTRUMPSAY-A"]
    assert poly_calls == ["parc-example-yes", "parc-missing"]
    assert "n: 2" in text


def test_settlement_client_failure_prints_the_type_only(capsys, monkeypatch) -> None:
    class Boom:
        def __init__(self) -> None:
            raise RuntimeError("https://secret.example/key")

    monkeypatch.setattr("polymarket.public_client.PolymarketPublicClient", Boom)
    from scripts.grok_paper_sidecar import _polymarket_settlement_lookup

    assert _polymarket_settlement_lookup() is None
    err = capsys.readouterr().err
    assert "error_type=RuntimeError" in err
    assert "secret" not in err
    assert "https://" not in err


def test_cash_refresh_writes_a_stale_file_and_keeps_a_bad_read(tmp_path: Path) -> None:
    path = tmp_path / "politics_cash_cents.json"
    stale = NOW - timedelta(seconds=7201)
    path.write_text(
        json.dumps(
            {
                "date": "2026-10-17",
                "last_cents": 3995,
                "start_cents": 3995,
                "ts": stale.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    kept = refresh_stale_cash(
        read_cents=lambda: None,
        path=path,
        now=NOW,
    )
    assert kept == "not_measured"
    assert json.loads(path.read_text(encoding="utf-8"))["last_cents"] == 3995
    fresh = refresh_stale_cash(read_cents=lambda: 3470, path=path, now=NOW)
    assert fresh == "refreshed"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["last_cents"] == 3470
    assert written["prev_cents"] == 3995
    boundary = refresh_stale_cash(
        read_cents=lambda: 1000,
        path=path,
        now=datetime.strptime(written["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        + timedelta(seconds=7200),
    )
    assert boundary == "fresh"
    assert json.loads(path.read_text(encoding="utf-8"))["last_cents"] == 3470


def test_balance_cents_rejects_a_missing_or_zero_balance() -> None:
    class Client:
        def __init__(self, payload) -> None:
            self.payload = payload

        def _request(self, method, endpoint):
            assert method == "GET"
            assert endpoint == "/portfolio/balance"
            return self.payload

    assert balance_cents(Client({"balance": 3470})) == 3470
    assert balance_cents(Client({"balance": 0})) is None
    assert balance_cents(Client({"balance": None})) is None
    assert balance_cents(Client({})) is None


def test_health_script_does_not_refresh_a_fixture_root() -> None:
    text = (ROOT / "scripts" / "bothealth.sh").read_text(encoding="utf-8")
    assert "BOTHEALTH_REPO_ROOT" in text
    assert "refresh_politics_cash.py" in text
    assert '[[ -z "${BOTHEALTH_REPO_ROOT:-}"' in text


def test_default_paper_db_path_is_not_the_politics_database() -> None:
    assert PAPER_DB_PATH.name == "grok_paper.db"
    assert "paper_trades" not in str(PAPER_DB_PATH)
