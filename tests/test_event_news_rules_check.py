"""Rules check skips only an explicit JSON mismatch. Live stays off."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import config as cfg_module
from scripts.event_news_rules_report import format_rules_report, rules_report
from utils.event_news_grok_veto import GROK_MODEL, XAI_CHAT_URL
from utils.event_news_research import EVENT_NEWS_COHORT_ID
from utils.event_news_rules_check import (
    ACT_COUNT_LOGIC,
    MODEL_COUNT_LOGIC,
    SAY_COUNT_LOGIC,
    event_news_rules_check_skip,
)


def _cohort(monkeypatch, cohort: str = EVENT_NEWS_COHORT_ID) -> None:
    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", cohort)


def _market(**overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "ticker": "KXTRUTHSOCIAL-26OCT24-B200",
        "rules_primary": "Count Truth Social posts in the contract week.",
        "rules": "",
        "contract_terms_url": "https://kalshi.com/rule/truth",
        "rules_url": None,
        "settlement_source_url": None,
        "settlement_sources": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _analysis(market: object) -> SimpleNamespace:
    return SimpleNamespace(market=market, side="yes")


def _response(content: str, *, response_id: str = "resp-rules-1") -> SimpleNamespace:
    body = {
        "id": response_id,
        "choices": [{"message": {"content": content}}],
    }
    return SimpleNamespace(json=lambda: body)


def _answer(**overrides: object) -> str:
    payload: dict[str, object] = {
        "what_counts": "posts in the contract week",
        "source": "https://kalshi.com/rule/truth",
        "cutoff_time": "2026-10-24T23:59:59-04:00",
        "trap": "deleted posts stay out",
        "mismatch": False,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_magicmock_url_does_not_fetch(monkeypatch, tmp_path) -> None:
    _cohort(monkeypatch)
    market = MagicMock()
    market.ticker = "KXTRUMPSAY-26OCT19-GOLF"
    fetch = MagicMock(side_effect=AssertionError("fetch"))
    post = MagicMock(side_effect=AssertionError("post"))
    skipped = event_news_rules_check_skip(
        _analysis(market),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=fetch,
        log_path=tmp_path / "rules.jsonl",
    )
    assert skipped is False
    fetch.assert_not_called()
    post.assert_not_called()
    assert not (tmp_path / "rules.jsonl").exists()


def test_http_source_is_fetched_and_a_mock_list_item_is_ignored(
    monkeypatch, tmp_path
) -> None:
    _cohort(monkeypatch)
    seen: dict[str, object] = {}

    def fetch(url: str) -> str:
        seen["url"] = url
        return "The week ends Friday at 11:59pm ET."

    def post(url, json=None, headers=None, timeout=None):
        seen["post_url"] = url
        seen["payload"] = json
        seen["headers"] = headers
        return _response(_answer(mismatch=True))

    market = _market(settlement_sources=[MagicMock(url=MagicMock())])
    skipped = event_news_rules_check_skip(
        _analysis(market),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=fetch,
        log_path=tmp_path / "rules.jsonl",
    )
    assert skipped is True
    assert seen["url"] == "https://kalshi.com/rule/truth"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == GROK_MODEL
    assert "tools" not in payload
    assert seen["post_url"] == XAI_CHAT_URL
    user = json.loads(payload["messages"][1]["content"])
    assert "11:59pm ET" in user["source_text"]
    assert "secret-key" not in (tmp_path / "rules.jsonl").read_text(encoding="utf-8")


def test_explicit_mismatch_is_stored_and_skips(monkeypatch, tmp_path) -> None:
    _cohort(monkeypatch)
    path = tmp_path / "rules.jsonl"

    def post(url, json=None, headers=None, timeout=None):
        preamble = "note {not json} "
        content = preamble + _answer(
            mismatch=True,
            what_counts="phrase hits",
            source="https://example.test/rules",
            cutoff_time="2026-10-17T04:00:00Z",
            trap="phrase market",
        )
        return _response(content, response_id="resp-mismatch")

    skipped = event_news_rules_check_skip(
        _analysis(_market()),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=lambda url: "source page",
        log_path=path,
    )
    assert skipped is True
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["mismatch"] is True
    assert row["response_id"] == "resp-mismatch"
    assert row["what_counts"] == "phrase hits"
    assert row["source"] == "https://example.test/rules"
    assert row["cutoff_time"] == "2026-10-17T04:00:00Z"
    assert row["trap"] == "phrase market"
    assert row["outcome"] is None
    assert "secret" not in path.read_text(encoding="utf-8")


def test_last_object_wins_and_a_string_true_does_not_skip(
    monkeypatch, tmp_path
) -> None:
    _cohort(monkeypatch)
    path = tmp_path / "rules.jsonl"

    def post(url, json=None, headers=None, timeout=None):
        first = _answer(mismatch=True, trap="first")
        last = _answer(mismatch="true", trap="last")
        return _response(first + last)

    skipped = event_news_rules_check_skip(
        _analysis(_market()),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=lambda url: "source page",
        log_path=path,
    )
    assert skipped is False
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["mismatch"] is False
    assert row["trap"] == "last"


def test_timeout_and_missing_text_and_missing_key_do_not_skip(
    monkeypatch, tmp_path
) -> None:
    _cohort(monkeypatch)
    path = tmp_path / "rules.jsonl"

    def boom(url, json=None, headers=None, timeout=None):
        raise TimeoutError("slow")

    skipped = event_news_rules_check_skip(
        _analysis(_market()),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=boom,
        fetch=lambda url: "source page",
        log_path=path,
    )
    assert skipped is False
    # A timeout is not a mismatch. The attempt is still journaled.
    timeout_row = json.loads(path.read_text(encoding="utf-8"))
    assert timeout_row["mismatch"] is False
    assert timeout_row["cost"] is None
    assert isinstance(timeout_row["elapsed_ms"], int)
    assert timeout_row["raw_reply"] == ""

    post = MagicMock(side_effect=AssertionError("post"))
    blank = event_news_rules_check_skip(
        _analysis(_market(rules_primary="", contract_terms_url=None)),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=MagicMock(side_effect=AssertionError("fetch")),
        log_path=path,
    )
    assert blank is False
    post.assert_not_called()

    post2 = MagicMock(side_effect=AssertionError("post"))
    missing_path = tmp_path / "missing-key.jsonl"
    missing_key = event_news_rules_check_skip(
        _analysis(_market()),
        live=False,
        environ={},
        post=post2,
        fetch=lambda url: "source page",
        log_path=missing_path,
    )
    assert missing_key is False
    post2.assert_not_called()
    assert not missing_path.exists()


def test_live_unset_and_other_cohort_do_not_call(monkeypatch, tmp_path) -> None:
    _cohort(monkeypatch)
    post = MagicMock(side_effect=AssertionError("post"))
    fetch = MagicMock(side_effect=AssertionError("fetch"))
    live_off = event_news_rules_check_skip(
        _analysis(_market()),
        live=True,
        environ={},
        post=post,
        fetch=fetch,
        log_path=tmp_path / "rules.jsonl",
    )
    assert live_off is False
    post.assert_not_called()
    fetch.assert_not_called()

    _cohort(monkeypatch, "other-cohort")
    other = event_news_rules_check_skip(
        _analysis(_market()),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=fetch,
        log_path=tmp_path / "rules.jsonl",
    )
    assert other is False
    post.assert_not_called()


def test_fetch_error_does_not_skip_when_rules_exist(monkeypatch, tmp_path) -> None:
    _cohort(monkeypatch)
    path = tmp_path / "rules.jsonl"
    seen: dict[str, object] = {}

    def post(url, json=None, headers=None, timeout=None):
        seen["payload"] = json
        return _response(_answer(mismatch=False))

    skipped = event_news_rules_check_skip(
        _analysis(_market()),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=MagicMock(side_effect=RuntimeError("network")),
        log_path=path,
    )
    assert skipped is False
    payload = seen["payload"]
    assert isinstance(payload, dict)
    user = json.loads(payload["messages"][1]["content"])
    assert user["source_text"] == ""
    assert user["rules"].startswith("Count Truth Social")
    assert json.loads(path.read_text(encoding="utf-8"))["mismatch"] is False


def _posted_logic(ticker: str, monkeypatch, tmp_path) -> str:
    _cohort(monkeypatch)
    seen: dict[str, object] = {}

    def post(url, json=None, headers=None, timeout=None):
        seen["payload"] = json
        body = {
            "id": "resp-series",
            "choices": [{"message": {"content": _answer(mismatch=False)}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 4},
        }
        return SimpleNamespace(json=lambda: body)

    event_news_rules_check_skip(
        _analysis(_market(ticker=ticker)),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=lambda url: "source page",
        log_path=tmp_path / "rules.jsonl",
    )
    payload = seen["payload"]
    assert isinstance(payload, dict)
    user = json.loads(payload["messages"][1]["content"])
    return str(user["model_count_logic"])


def test_series_rubric_does_not_send_truth_social_logic_to_act_or_say(
    monkeypatch, tmp_path
) -> None:
    truth = _posted_logic("KXTRUTHSOCIAL-26OCT24-B200", monkeypatch, tmp_path)
    say = _posted_logic("KXTRUMPSAY-26OCT19-GOLF", monkeypatch, tmp_path)
    act = _posted_logic("KXTRUMPACT-26OCT11-T4", monkeypatch, tmp_path)
    assert truth == MODEL_COUNT_LOGIC
    assert say == SAY_COUNT_LOGIC
    assert act == ACT_COUNT_LOGIC
    assert "The model counts Truth Social posts" not in say
    assert "The model counts Truth Social posts" not in act
    assert "presidential actions" not in say
    assert "phrase hits" not in act


def test_unknown_series_does_not_call_the_model(monkeypatch, tmp_path) -> None:
    _cohort(monkeypatch)
    post = MagicMock(side_effect=AssertionError("post"))
    skipped = event_news_rules_check_skip(
        _analysis(_market(ticker="KXCPI-26OCT-T1")),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=lambda url: "source page",
        log_path=tmp_path / "rules.jsonl",
    )
    assert skipped is False
    post.assert_not_called()
    assert not (tmp_path / "rules.jsonl").exists()


def test_journal_stores_raw_reply_latency_and_null_cost(monkeypatch, tmp_path) -> None:
    _cohort(monkeypatch)
    path = tmp_path / "rules.jsonl"
    reply = _answer(mismatch=False, what_counts="posts")

    def post(url, json=None, headers=None, timeout=None):
        return _response(reply, response_id="resp-cost")

    skipped = event_news_rules_check_skip(
        _analysis(_market()),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=lambda url: "source page",
        log_path=path,
    )
    assert skipped is False
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["raw_reply"] == reply
    assert isinstance(row["elapsed_ms"], int)
    assert row["elapsed_ms"] >= 0
    assert "cost" in row
    assert row["cost"] is None
    assert "secret-key" not in path.read_text(encoding="utf-8")


def test_journal_stores_numeric_usage_cost(monkeypatch, tmp_path) -> None:
    _cohort(monkeypatch)
    path = tmp_path / "rules.jsonl"

    def post(url, json=None, headers=None, timeout=None):
        body = {
            "id": "resp-usage",
            "choices": [{"message": {"content": _answer(mismatch=False)}}],
            "usage": {
                "cost_usd": 0.012,
                "prompt_tokens": 20,
                "completion_tokens": 6,
            },
        }
        return SimpleNamespace(json=lambda: body)

    event_news_rules_check_skip(
        _analysis(_market()),
        live=False,
        environ={"XAI_API_KEY": "secret-key"},
        post=post,
        fetch=lambda url: "source page",
        log_path=path,
    )
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["cost"] == pytest.approx(0.012)
    assert row["prompt_tokens"] == 20
    assert row["completion_tokens"] == 6


def test_empty_rules_report_does_not_recommend_live(tmp_path) -> None:
    report = rules_report(log_path=tmp_path / "missing.jsonl")
    assert report["n_rows"] == 0
    assert report["n_flagged"] == 0
    assert report["n"] == 0
    assert report["precision"] == "not_measured"
    assert report["flagged_fee_net"] == "not_measured"
    assert report["recommend_live"] is False
    text = format_rules_report(report)
    assert "recommend live: no" in text
    assert "at least 20" in text


def test_rules_report_recommends_live_only_at_twenty_losing_flags(tmp_path) -> None:
    path = tmp_path / "rules.jsonl"
    rows = []
    outcomes: dict[str, Decimal] = {}
    for index in range(20):
        ticker = f"KXTRUTHSOCIAL-RULE-{index}"
        rows.append(
            {
                "ticker": ticker,
                "mismatch": True,
                "response_id": f"resp-{index}",
                "outcome": None,
            }
        )
        outcomes[ticker] = Decimal("-1.00")
    rows.append(
        {
            "ticker": "KXTRUMPSAY-KEEP",
            "mismatch": False,
            "response_id": "resp-keep",
            "outcome": None,
        }
    )
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    full = rules_report(log_path=path, outcomes=outcomes)
    assert full["n_flagged"] == 20
    assert full["n"] == 20
    assert full["precision"] == 1.0
    assert full["flagged_fee_net"] == Decimal("-20.00")
    assert full["recommend_live"] is True

    short = rules_report(
        log_path=path,
        outcomes={ticker: Decimal("-1.00") for ticker in list(outcomes)[:19]},
    )
    assert short["n"] == 19
    assert short["recommend_live"] is False


def test_rules_report_uses_a_stored_outcome_when_no_map_is_passed(tmp_path) -> None:
    path = tmp_path / "rules.jsonl"
    path.write_text(
        json.dumps(
            {
                "ticker": "KXTRUTHSOCIAL-26OCT03-B129",
                "mismatch": True,
                "outcome": "-7.5226",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = rules_report(log_path=path)
    assert report["n"] == 1
    assert report["precision"] == 1.0
    assert report["flagged_fee_net"] == Decimal("-7.5226")
    assert report["recommend_live"] is False


@pytest.mark.asyncio
async def test_live_unset_does_not_call_rules_and_still_posts(
    monkeypatch, tmp_path
) -> None:
    from tests.test_event_news_grok_flags import (
        _analysis as live_analysis,
        _band_flags_unset,
        _executor,
        _liquidity,
        _plan_then_live,
    )
    from trading.execution_terms import FinalExecutionTerms
    from unittest.mock import patch

    _band_flags_unset(monkeypatch)
    monkeypatch.delenv("EVENT_NEWS_GROK_RULES_CHECK", raising=False)
    executor = _executor(monkeypatch, tmp_path)
    analysis = live_analysis()
    analysis.executed_price_cents = 36
    checker = MagicMock(side_effect=AssertionError("live rules check"))
    with patch(
        "utils.event_news_rules_check.event_news_rules_check_skip",
        checker,
    ):
        _plan, reason, order_id = await _plan_then_live(
            executor,
            analysis,
            FinalExecutionTerms(36, 8, 2.88),
            _liquidity("0.36"),
            lambda *args, **kwargs: True,
        )
    assert reason is None
    assert order_id == "ord-1"
    checker.assert_not_called()
    executor._rest.place_limit_order.assert_called_once()
    posted = executor._rest.place_limit_order.call_args.kwargs
    assert posted["limit_price"] == 36
    assert posted["count"] == 8


@pytest.mark.asyncio
async def test_live_rules_mismatch_does_not_post_when_the_flag_is_on(
    monkeypatch, tmp_path
) -> None:
    from tests.test_event_news_grok_flags import (
        _analysis as live_analysis,
        _band_flags_unset,
        _executor,
        _liquidity,
        _plan_then_live,
    )
    from trading.execution_terms import FinalExecutionTerms
    from unittest.mock import patch

    _band_flags_unset(monkeypatch)
    monkeypatch.setenv("EVENT_NEWS_GROK_RULES_CHECK", "true")
    executor = _executor(monkeypatch, tmp_path)
    analysis = live_analysis()
    analysis.executed_price_cents = 36
    with patch(
        "utils.event_news_rules_check.event_news_rules_check_skip",
        return_value=True,
    ):
        _plan, reason, order_id = await _plan_then_live(
            executor,
            analysis,
            FinalExecutionTerms(36, 8, 2.88),
            _liquidity("0.36"),
            lambda *args, **kwargs: True,
        )
    assert reason is None
    assert order_id is None
    executor._rest.place_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_paper_rules_mismatch_does_not_record(monkeypatch, tmp_path) -> None:
    from tests.test_event_news_grok_flags import (
        _analysis as paper_analysis,
        _band_flags_unset,
        _executor,
        _liquidity,
        _stub_equity,
    )
    from trading.execution_terms import FinalExecutionTerms
    from unittest.mock import AsyncMock, patch

    _band_flags_unset(monkeypatch)
    monkeypatch.setattr(cfg_module.cfg, "enable_fee_net_paper_accounting", False)
    executor = _executor(monkeypatch, tmp_path, paper=True)
    _stub_equity(executor)
    analysis = paper_analysis()
    analysis.executed_price_cents = 60
    executor._paper.record_trade.return_value = "paper-rules"
    with (
        patch(
            "utils.event_news_grok_veto.event_news_paper_grounding_skip",
            return_value=False,
        ),
        patch(
            "utils.event_news_rules_check.event_news_rules_check_skip",
            return_value=True,
        ),
        patch(
            "trading.executor.final_execution_terms",
            return_value=FinalExecutionTerms(60, 8, 4.80),
        ),
        patch(
            "trading.executor.fetch_kalshi_execution_liquidity",
            new=AsyncMock(return_value=_liquidity("0.60")),
        ),
    ):
        plan, reason = await executor._final_execution_plan(analysis)
        trade_id = await executor._execute_paper(analysis, execution_plan=plan)
    assert reason is None and trade_id is None
    executor._paper.record_trade.assert_not_called()


@pytest.mark.asyncio
async def test_paper_rules_timeout_still_records(monkeypatch, tmp_path) -> None:
    from tests.test_event_news_grok_flags import (
        _analysis as paper_analysis,
        _band_flags_unset,
        _executor,
        _liquidity,
        _stub_equity,
    )
    from trading.execution_terms import FinalExecutionTerms
    from unittest.mock import AsyncMock, patch

    _band_flags_unset(monkeypatch)
    monkeypatch.setattr(cfg_module.cfg, "enable_fee_net_paper_accounting", False)
    executor = _executor(monkeypatch, tmp_path, paper=True)
    _stub_equity(executor)
    analysis = paper_analysis()
    analysis.executed_price_cents = 60
    executor._paper.record_trade.return_value = "paper-rules"
    with (
        patch(
            "utils.event_news_grok_veto.event_news_paper_grounding_skip",
            return_value=False,
        ),
        patch(
            "utils.event_news_rules_check.event_news_rules_check_skip",
            return_value=False,
        ),
        patch(
            "trading.executor.final_execution_terms",
            return_value=FinalExecutionTerms(60, 8, 4.80),
        ),
        patch(
            "trading.executor.fetch_kalshi_execution_liquidity",
            new=AsyncMock(return_value=_liquidity("0.60")),
        ),
    ):
        plan, _reason = await executor._final_execution_plan(analysis)
        trade_id = await executor._execute_paper(analysis, execution_plan=plan)
    assert trade_id == "paper-rules"
