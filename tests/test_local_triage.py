"""Paper-only local triage. No network except a local fake server. No orders."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from scripts.local_triage_shadow import (
    assert_shadow_dir,
    load_human_labels,
    run_shadow,
    schema_rates,
    sports_prefixes,
)
from utils.local_triage import (
    DOMAINS,
    SETTLEMENTS,
    DiscoveryFilters,
    apply_deterministic_axes,
    det_match_rates,
    deterministic_axes,
    human_axis_scores,
    review_axis,
    screen_market,
    select_pilot,
    touch_depth,
)
from utils.local_triage_classifier import (
    LocalClassifier,
    is_paid_research_host,
    parse_classification,
    schema_mode_from_probe,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
FILTERS = DiscoveryFilters()
VALID = {
    "domain": "politics",
    "settlement": "countable",
    "settlement_source": "Factbase",
    "rationale": "The rules count posts.",
    "ambiguity": "none",
}


def _close(delta: timedelta) -> str:
    return (NOW + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _market(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticker": "KXTEST-26NOV20-A",
        "event_ticker": "KXTEST-26NOV20",
        "series_ticker": "KXTEST",
        "status": "active",
        "title": "Example title that is not the class",
        "category": "Politics",
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.42",
        "no_bid_dollars": "0.58",
        "no_ask_dollars": "0.60",
        "volume_24h_fp": "800",
        "open_interest_fp": "2000",
        "close_time": _close(timedelta(days=2)),
        "rules_primary": "Resolves on the count of posts.",
        "yes_ask_size_fp": "8.00",
    }
    row.update(overrides)
    return row


def _reason(**overrides: Any) -> str:
    return str(screen_market(_market(**overrides), FILTERS, now=NOW)["rejection_reason"])


def _levels(quantity: str | None) -> tuple[Any, ...]:
    if quantity is None:
        return ()

    class _Level:
        def __init__(self, qty: str) -> None:
            self.quantity = Decimal(qty)

    return (_Level(quantity),)


class _Book:
    def __init__(self, yes_qty: str | None, no_qty: str | None) -> None:
        self.yes_bids = _levels(yes_qty)
        self.no_bids = _levels(no_qty)


class _Pages:
    def __init__(
        self,
        markets: list[Any],
        series: list[dict[str, Any]],
        second: list[Any] | None = None,
    ) -> None:
        self.markets = markets
        self.series = series
        self.second = second
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._market_pages = 0

    def _request(self, method: str, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert method == "GET"
        assert "order" not in endpoint
        assert not endpoint.startswith("/portfolio")
        query = dict(params or {})
        self.calls.append((endpoint, query))
        if endpoint == "/markets":
            self._market_pages += 1
            if self._market_pages == 1 and self.second is not None:
                return {"markets": self.markets, "cursor": "next"}
            if self._market_pages == 2:
                assert query.get("cursor") == "next"
                assert query.get("status") == "open"
                assert query.get("mve_filter") == "exclude"
                return {"markets": self.second, "cursor": ""}
            return {"markets": self.markets, "cursor": ""}
        if endpoint == "/series":
            return {"series": self.series, "cursor": ""}
        raise AssertionError(endpoint)


def _classifier(opener: Any, *, base_url: str = "http://127.0.0.1:9/v1", model: str = "local-test") -> LocalClassifier:
    return LocalClassifier(
        base_url=base_url,
        model_id=model,
        runtime="mlx",
        timeout_s=0.05,
        opener=opener,
        clock=lambda: NOW,
    )


def _response(body: dict[str, Any]) -> Any:
    encoded = json.dumps(body).encode("utf-8")

    class _Resp:
        status = 200

        def read(self) -> bytes:
            return encoded

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_args: Any) -> bool:
            return False

    def _open(request: Any, timeout: float = 0) -> _Resp:
        del request, timeout
        return _Resp()

    return _open


def _envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


def test_filter_order_and_bounds() -> None:
    """The first failing screen reason is the one that is kept."""
    assert _reason() == ""
    assert _reason(status="finalized", mve_collection_ticker="MVE") == "not_active"
    assert _reason(mve_collection_ticker="MVE-1") == "mve_payload"
    assert _reason(mve_collection_ticker="") == ""
    assert _reason(market_type="mve") == "mve_payload"
    assert _reason(yes_bid_dollars=None) == "missing_quote"
    crossed = _market(yes_bid_dollars="0.50", yes_ask_dollars="0.48", no_bid_dollars="0.52", no_ask_dollars="0.50")
    assert screen_market(crossed, FILTERS, now=NOW)["rejection_reason"] == "crossed"
    assert _reason(volume_24h_fp=None, open_interest_fp=None) == "volume_missing"
    assert _reason(volume_24h_fp="499", yes_ask_dollars="0.20", yes_bid_dollars="0.10") == "volume_below_min"
    assert _reason(volume_24h_fp="500") == ""
    assert _reason(open_interest_fp=None) == "open_interest_missing"
    assert _reason(open_interest_fp="999") == "open_interest_below_min"
    assert _reason(open_interest_fp="1000") == ""
    wide = _market(yes_bid_dollars="0.40", yes_ask_dollars="0.44", no_bid_dollars="0.56", no_ask_dollars="0.60")
    assert screen_market(wide, FILTERS, now=NOW)["rejection_reason"] == "spread_above_max"
    tight = _market(yes_bid_dollars="0.40", yes_ask_dollars="0.43", no_bid_dollars="0.57", no_ask_dollars="0.60")
    assert screen_market(tight, FILTERS, now=NOW)["rejection_reason"] == ""
    assert screen_market(_market(yes_bid_dollars="0.42", yes_ask_dollars="0.42"), FILTERS, now=NOW)["spread_cents"] == 0
    assert _reason(close_time="") == "close_missing"
    assert _reason(close_time=_close(timedelta(hours=6) - timedelta(seconds=1))) == "close_too_soon"
    assert _reason(close_time=_close(timedelta(hours=6))) == ""
    assert _reason(close_time=_close(timedelta(days=14))) == ""
    assert _reason(close_time=_close(timedelta(days=14, seconds=1))) == "close_too_late"
    assert _reason(yes_ask_dollars="0.09", yes_bid_dollars="0.08") == "yes_ask_outside_band"
    assert _reason(yes_ask_dollars="0.91", yes_bid_dollars="0.90") == "yes_ask_outside_band"
    assert _reason(yes_ask_dollars="0.10", yes_bid_dollars="0.10") == ""
    assert _reason(yes_ask_dollars="0.90", yes_bid_dollars="0.90") == ""


def test_best_market_per_event_and_provisional_class() -> None:
    """Lowest spread wins. The loser stays unclassified. Title text is not a settlement."""
    from utils.local_triage import keep_best_per_event

    tight = screen_market(
        _market(ticker="KXTEST-26NOV20-B", yes_bid_dollars="0.41", yes_ask_dollars="0.42"),
        FILTERS,
        now=NOW,
    )
    wide = screen_market(_market(ticker="KXTEST-26NOV20-A", volume_24h_fp="9000"), FILTERS, now=NOW)
    other_event = screen_market(
        _market(ticker="KXOTHER-26NOV20-A", event_ticker="KXOTHER-26NOV20", series_ticker="KXOTHER"),
        FILTERS,
        now=NOW,
    )
    rows = [wide, tight, other_event]
    keep_best_per_event(rows)
    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["KXTEST-26NOV20-B"]["rejection_reason"] == ""
    assert by_ticker["KXTEST-26NOV20-A"]["rejection_reason"] == "not_best_in_event"
    apply_deterministic_axes(rows, sports_prefixes=("KXNFL",))
    assert by_ticker["KXTEST-26NOV20-A"]["deterministic_domain"] == ""
    assert by_ticker["KXTEST-26NOV20-B"]["deterministic_domain"] == "politics"
    assert by_ticker["KXTEST-26NOV20-B"]["deterministic_settlement"] == "countable"
    assert by_ticker["KXTEST-26NOV20-B"]["deterministic_ambiguity"] == "none"

    same_spread_low_volume = screen_market(_market(ticker="KXTEST-26NOV20-C", volume_24h_fp="600"), FILTERS, now=NOW)
    same_spread_high_volume = screen_market(_market(ticker="KXTEST-26NOV20-D", volume_24h_fp="700"), FILTERS, now=NOW)
    tied = [same_spread_low_volume, same_spread_high_volume]
    keep_best_per_event(tied)
    assert same_spread_high_volume["rejection_reason"] == ""
    assert same_spread_low_volume["rejection_reason"] == "not_best_in_event"


def test_heuristic_is_provisional_and_title_only_is_ambiguous() -> None:
    """Sports is a domain. A count inside a sports rule stays countable. A title is not a settlement."""
    rules = {
        "series_ticker": "KXTEST",
        "category": "Politics",
        "title": "tweet count championship",
        "rules_primary": "",
        "rules_secondary": "",
        "settlement_sources": [],
    }
    assert deterministic_axes(rules, sports_prefixes=("KXNFL",)) == ("politics", "ambiguous", "insufficient_evidence")
    sports = dict(rules, series_ticker="KXNFLGAME-26", rules_primary="Resolves on the count of posts.")
    assert deterministic_axes(sports, sports_prefixes=("KXNFL",)) == ("sports", "countable", "none")
    category = dict(rules, category="Sports", rules_primary="Resolves on the count of posts.")
    assert deterministic_axes(category, sports_prefixes=("KXNFL",)) == ("sports", "countable", "none")
    both = dict(rules, rules_primary="The federal register count of posts settles the market.")
    assert deterministic_axes(both, sports_prefixes=("KXNFL",)) == ("politics", "ambiguous", "ambiguous")
    other = dict(rules, rules_primary="Resolves when the named bill is signed.")
    assert deterministic_axes(other, sports_prefixes=("KXNFL",)) == ("politics", "other", "none")
    poll = dict(rules, series_ticker="KXAPRPOTUS", rules_primary="Resolves on the approval rating.")
    assert deterministic_axes(poll, sports_prefixes=sports_prefixes()) == ("politics", "opinion", "none")
    assert "KXAPRPOTUS" not in sports_prefixes()
    assert "KXNFL" in sports_prefixes()


def test_missing_depth_stays_missing() -> None:
    """An empty side is null. A list size of 8 is not a book, and 8 is not assumed."""
    blank = touch_depth(_Book(None, None))
    assert blank == {
        "yes_bid_size": None,
        "yes_ask_size": None,
        "no_bid_size": None,
        "no_ask_size": None,
    }
    one_side = touch_depth(_Book("4.00", None))
    assert one_side["yes_bid_size"] == "4.00"
    assert one_side["no_ask_size"] == "4.00"
    assert one_side["yes_ask_size"] is None
    assert one_side["no_bid_size"] is None
    zero = touch_depth(_Book("0", "8"))
    assert zero["yes_bid_size"] is None
    assert zero["no_ask_size"] is None
    screened = screen_market(_market(), FILTERS, now=NOW)
    assert screened["yes_ask_size"] is None
    assert screened["yes_bid_size"] is None


def test_review_keeps_both_classes_and_disagreement_stays_ambiguous() -> None:
    """A disagreement changes only the axis that disagreed. The other axis can agree."""
    assert review_axis("countable", "ok", "lookup", SETTLEMENTS) == "ambiguous"
    assert review_axis("countable", "ok", "countable", SETTLEMENTS) == "countable"
    assert review_axis("sports", "ok", "sports", DOMAINS) == "sports"
    assert review_axis("countable", "malformed", "countable", SETTLEMENTS) == "local_unavailable"
    assert review_axis("countable", "unconfigured", "", SETTLEMENTS) == "local_unavailable"


def test_parse_rejects_probability_and_accepts_schema() -> None:
    """A probability field is malformed. Ambiguous and insufficient evidence are explicit."""
    raw = json.dumps(_envelope(VALID))
    assert parse_classification(raw) == VALID
    leaked = dict(VALID, p_raw=0.5)
    assert parse_classification(json.dumps(_envelope(leaked))) is None
    assert parse_classification(json.dumps(_envelope(dict(VALID, p_final=0.5)))) is None
    assert parse_classification(json.dumps(_envelope(dict(VALID, probability=0.5)))) is None
    assert parse_classification(json.dumps(_envelope(dict(VALID, trade="buy")))) is None
    assert parse_classification(json.dumps(_envelope(dict(VALID, side="yes")))) is None
    assert parse_classification(json.dumps(_envelope(dict(VALID, order="1")))) is None
    sports_count = dict(VALID, domain="sports", settlement="countable", rationale="Rushing attempts are a count.")
    assert parse_classification(json.dumps(sports_count)) == sports_count
    thin = {
        "domain": "ambiguous",
        "settlement": "ambiguous",
        "settlement_source": "",
        "rationale": "Rules are missing.",
        "ambiguity": "insufficient_evidence",
    }
    assert parse_classification(json.dumps(thin)) == thin
    assert parse_classification(json.dumps(dict(VALID, ambiguity="insufficient_evidence"))) is None
    assert parse_classification(json.dumps(dict(VALID, domain="ambiguous"))) is None
    assert parse_classification("not-json") is None


def test_unconfigured_and_paid_host_make_no_request() -> None:
    """No base URL, no model, or an xAI host never opens a socket."""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("socket opened")

    missing = LocalClassifier(base_url="", model_id="", runtime="", timeout_s=0.05, opener=_boom, clock=lambda: NOW)
    record = missing.classify({"ticker": "KXTEST-26NOV20-A", "rule_version": "missing"})
    assert record["outcome"] == "unconfigured"
    assert record["model_id"] == ""
    assert record["runtime"] == "unconfigured"
    assert "p_raw" not in record
    paid = _classifier(_boom, base_url="https://api.x.ai/v1", model="grok-4")
    refused = paid.classify({"ticker": "KXTEST-26NOV20-A"})
    assert refused["outcome"] == "refused_paid_host"
    assert is_paid_research_host("https://api.x.ai/v1")
    assert not is_paid_research_host("http://127.0.0.1:8080/v1")


def test_malformed_retries_once_then_fails_and_timeout_skips_the_rest() -> None:
    """One bad body is retried. A timeout does not wait out the rest of the shortlist."""
    calls: list[str] = []

    def _open(request: Any, timeout: float = 0) -> Any:
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body["response_format"]["type"])
        if len(calls) == 1:
            return _response(_envelope(dict(VALID, p_raw=0.4)))(request)
        fixed = dict(VALID)
        fixed["settlement"] = "lookup"
        fixed["rationale"] = "A published lookup."
        return _response(_envelope(fixed))(request)

    client = _classifier(_open)
    first = client.classify(_market())
    assert first["outcome"] == "ok"
    assert first["retries"] == 1
    assert first["settlement"] == "lookup"
    assert first["retry_outcome"] == "ok_on_retry"
    assert first["parser_failure"] == "probability_field"
    assert calls == ["json_schema", "json_schema"]
    assert "json_object" not in calls
    assert "p_raw" not in first

    def _always_bad(request: Any, timeout: float = 0) -> Any:
        del timeout
        return _response(_envelope({"domain": "politics", "settlement": "countable", "probability": 0.2}))(request)

    bad = _classifier(_always_bad).classify(_market())
    assert bad["outcome"] == "malformed"
    assert bad["domain"] == ""
    assert bad["settlement"] == ""
    assert bad["retries"] == 1
    assert bad["retry_outcome"] == "malformed"

    timeouts = {"n": 0}

    def _hang(request: Any, timeout: float = 0) -> Any:
        del request, timeout
        timeouts["n"] += 1
        raise TimeoutError("hung")

    hung = _classifier(_hang)
    assert hung.classify(_market())["outcome"] == "timeout"
    assert hung.classify(_market(ticker="KXTEST-26NOV20-B"))["outcome"] == "timeout"
    assert timeouts["n"] == 2

    from urllib.error import URLError

    refused = {"n": 0}

    def _refuse(request: Any, timeout: float = 0) -> Any:
        del request, timeout
        refused["n"] += 1
        raise URLError("connection refused")

    dead = _classifier(_refuse)
    assert dead.classify(_market())["outcome"] == "endpoint_unavailable"
    assert dead.classify(_market(ticker="KXTEST-26NOV20-B"))["outcome"] == "endpoint_unavailable"
    assert refused["n"] == 1


def test_series_sources_attach_when_the_market_already_has_rules(tmp_path: Path) -> None:
    """A market rule does not block the series settlement source. The market rule stays."""
    market = _market(rules_primary="Resolves when the named bill is signed.")
    pages = _Pages(
        [market],
        [
            {
                "ticker": "KXTEST",
                "title": "Bill",
                "category": "Politics",
                "rules_primary": "Series rules that must not replace the market rule.",
                "settlement_sources": [{"name": "Congress.gov"}],
            }
        ],
    )
    details: list[str] = []

    def _detail(ticker: str) -> dict[str, str]:
        details.append(ticker)
        return {"rules_primary": "Fetched rule", "settlement_sources": [{"name": "Should not win"}]}

    funnel = run_shadow(
        pages,
        now=NOW,
        out_dir=tmp_path / "local_triage",
        filters=FILTERS,
        classifier=LocalClassifier(base_url="", model_id="", runtime="mlx", timeout_s=0.05, clock=lambda: NOW),
        sports_prefixes=(),
        orderbook_for=lambda _ticker: _Book(None, None),
        market_for=_detail,
    )
    assert details == []
    discovery = Path(funnel["paths"]["discovery"]).read_text(encoding="utf-8").splitlines()
    row = json.loads(discovery[0])
    assert row["rules_primary"] == "Resolves when the named bill is signed."
    assert row["settlement_sources"] == ["Congress.gov"]
    assert row["rule_version"] != "missing"
    assert funnel["missing"]["rules"] == 0


def test_shadow_funnel_does_not_fabricate_depth_or_leave_the_directory(tmp_path: Path) -> None:
    """Rejected rows are not booked. A list size of 8 does not become depth. Files stay in the shadow dir."""
    kept = _market(rules_primary="")
    loser = _market(ticker="KXTEST-26NOV20-Z", volume_24h_fp="100")
    pages = _Pages(
        [kept, loser, "not-a-market"],
        [
            {
                "ticker": "KXTEST",
                "title": "Posts",
                "category": "Politics",
                "rules_primary": "Resolves on the count of posts.",
                "settlement_sources": [{"name": "Factbase"}],
            }
        ],
    )
    books: list[str] = []
    details: list[str] = []

    def _book(ticker: str) -> _Book:
        books.append(ticker)
        if ticker.endswith("-A"):
            raise RuntimeError("book down")
        return _Book("4.00", "6.00")

    def _detail(ticker: str) -> dict[str, str]:
        details.append(ticker)
        return {"rules_primary": "Should not be needed once series rules exist."}

    sentinel = tmp_path / "grok_veto_decisions.jsonl"
    sentinel.write_text("keep\n", encoding="utf-8")
    out = tmp_path / "local_triage"
    funnel = run_shadow(
        pages,
        now=NOW,
        out_dir=out,
        filters=FILTERS,
        classifier=LocalClassifier(base_url="", model_id="", runtime="mlx", timeout_s=0.05, clock=lambda: NOW),
        sports_prefixes=("KXNFL",),
        orderbook_for=_book,
        market_for=_detail,
    )
    assert pages.calls[0][1]["status"] == "open"
    assert pages.calls[0][1]["mve_filter"] == "exclude"
    assert books == ["KXTEST-26NOV20-A"]
    assert details == []
    assert funnel["shortlist"] == 1
    assert funnel["shortlist_before_sports"] == 1
    assert funnel["sports_excluded"] == 0
    assert funnel["pilot"] == 1
    assert funnel["passed_filters"] == 1
    assert funnel["rejected"]["volume_below_min"] == 1
    assert funnel["local_outcomes"] == {"unconfigured": 1}
    assert funnel["review_domains"] == {"local_unavailable": 1}
    assert funnel["review_settlements"] == {"local_unavailable": 1}
    assert funnel["deterministic_domains"] == {"politics": 1}
    assert funnel["deterministic_settlements"] == {"countable": 1}
    assert funnel["schema_enforced"] is False
    assert funnel["missing"]["depth"] == 1
    assert funnel["missing"]["rules"] == 0
    discovery = Path(funnel["paths"]["discovery"]).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in discovery if line.strip()]
    kept_row = next(row for row in rows if row["ticker"] == "KXTEST-26NOV20-A")
    assert kept_row["yes_bid_size"] is None
    assert kept_row["yes_ask_size"] is None
    assert kept_row["deterministic_domain"] == "politics"
    assert kept_row["deterministic_settlement"] == "countable"
    assert kept_row["deterministic_ambiguity"] == "none"
    assert kept_row["track"] == "main"
    assert kept_row["rules_primary"] == "Resolves on the count of posts."
    assert kept_row["settlement_sources"] == ["Factbase"]
    assert kept_row["rule_version"] != "missing"
    loser_row = next(row for row in rows if row["ticker"] == "KXTEST-26NOV20-Z")
    assert loser_row["yes_ask_size"] is None
    assert loser_row["rejection_reason"] == "volume_below_min"
    assert "8.00" not in json.dumps(kept_row)
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert Path(funnel["paths"]["classification"]).parent == out.resolve()
    cache = json.loads(Path(funnel["paths"]["series_cache"]).read_text(encoding="utf-8"))
    assert "raw_payload" not in cache[0]
    review = json.loads(Path(funnel["paths"]["review"]).read_text(encoding="utf-8"))
    assert review["rows"][0]["human_domain"] is None
    assert review["rows"][0]["human_settlement"] is None
    assert review["rows"][0]["review_domain"] == "local_unavailable"
    assert review["rows"][0]["review_settlement"] == "local_unavailable"
    with pytest.raises(RuntimeError):
        assert_shadow_dir(tmp_path / "grok_edge_calls.jsonl")


def test_disagreement_in_the_shadow_sample_stays_ambiguous(tmp_path: Path) -> None:
    """Equal labels stay provisional. Different labels stay ambiguous. Neither class is replaced."""
    calls = {"n": 0}

    def _open(request: Any, timeout: float = 0) -> Any:
        del timeout
        calls["n"] += 1
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "studio-model"
        assert urlparse(request.full_url).hostname == "127.0.0.1"
        assert body["response_format"]["type"] == "json_schema"
        return _response(_envelope({
            "domain": "politics",
            "settlement": "lookup",
            "settlement_source": "Federal Register",
            "rationale": "The model read a lookup.",
            "ambiguity": "none",
        }))(request)

    pages = _Pages(
        [_market()],
        [],
        second=[_market(ticker="KXTEST-26NOV20-B", event_ticker="KXTEST-26NOV21")],
    )
    funnel = run_shadow(
        pages,
        now=NOW,
        out_dir=tmp_path / "shadow",
        filters=FILTERS,
        classifier=_classifier(_open, base_url="http://127.0.0.1:9/v1", model="studio-model"),
        sports_prefixes=("KXNFL",),
        orderbook_for=lambda ticker: _Book("12.50", "3.00"),
        market_for=lambda ticker: None,
    )
    assert calls["n"] == 2
    assert funnel["local_domains"] == {"politics": 2}
    assert funnel["local_settlements"] == {"lookup": 2}
    assert funnel["review_domains"] == {"politics": 2}
    assert funnel["review_settlements"] == {"ambiguous": 2}
    assert funnel["schema_enforced"] is False
    assert funnel["missing"]["depth"] == 0
    review = json.loads(Path(funnel["paths"]["review"]).read_text(encoding="utf-8"))
    assert {row["deterministic_domain"] for row in review["rows"]} == {"politics"}
    assert {row["deterministic_settlement"] for row in review["rows"]} == {"countable"}
    assert {row["local_domain"] for row in review["rows"]} == {"politics"}
    assert {row["local_settlement"] for row in review["rows"]} == {"lookup"}
    assert {row["review_domain"] for row in review["rows"]} == {"politics"}
    assert {row["review_settlement"] for row in review["rows"]} == {"ambiguous"}
    assert {row["human_domain"] for row in review["rows"]} == {None}
    assert {row["human_settlement"] for row in review["rows"]} == {None}
    classified = [
        json.loads(line)
        for line in Path(funnel["paths"]["classification"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert classified[0]["model_id"] == "studio-model"
    assert classified[0]["runtime"] == "mlx"
    assert len(classified[0]["prompt_hash"]) == 64
    assert len(classified[0]["response_hash"]) == 64
    assert classified[0]["rule_version"] == classified[0]["rule_version"]
    assert "p_raw" not in classified[0]
    assert "p_final" not in classified[0]
    discovery = [
        json.loads(line)
        for line in Path(funnel["paths"]["discovery"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kept = next(row for row in discovery if row["ticker"] == "KXTEST-26NOV20-A")
    assert kept["yes_bid_size"] == "12.50"
    assert kept["yes_ask_size"] == "3.00"
    assert kept["no_bid_size"] == "3.00"
    assert kept["no_ask_size"] == "12.50"
    assert kept["deterministic_domain"] == "politics"
    assert kept["deterministic_settlement"] == "countable"
    assert kept["deterministic_ambiguity"] == "none"


def test_fake_local_server_and_no_external_host() -> None:
    """CI classifies against a loopback server. The request never names a paid host."""
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            seen.append(json.loads(self.rfile.read(length).decode("utf-8")))
            raw = json.dumps(_envelope(VALID)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        client = LocalClassifier(
            base_url=f"http://{host}:{port}/v1",
            model_id="fake-local",
            runtime="ollama",
            timeout_s=2,
            clock=lambda: NOW,
        )
        record = client.classify(_market())
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert record["outcome"] == "ok"
    assert record["domain"] == "politics"
    assert record["settlement"] == "countable"
    assert record["schema_enforced"] is False
    assert "class" not in record
    assert record["runtime"] == "ollama"
    assert seen[0]["model"] == "fake-local"
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert "api.x.ai" not in json.dumps(seen)


def test_local_output_cannot_reach_orders_admission_or_xai() -> None:
    """The scaffold is not imported by execution, and its source has no order or xAI URL."""
    modules = (
        ROOT / "utils" / "local_triage.py",
        ROOT / "utils" / "local_triage_classifier.py",
        ROOT / "scripts" / "local_triage_shadow.py",
    )
    for path in modules:
        text = path.read_text(encoding="utf-8")
        assert "place_limit_order" not in text
        assert "/v1/orders" not in text
        assert "api.x.ai" not in text
        assert "/portfolio/orders" not in text
        assert "/portfolio/events/orders" not in text
        assert "XAI_CHAT_URL" not in text
        assert "XAI_RESPONSES_URL" not in text
    for folder in ("trading", "tasks"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "local_triage" not in text
    shadow = (ROOT / "scripts" / "local_triage_shadow.py").read_text(encoding="utf-8")
    assert "get_universe" not in shadow
    assert 'add_argument("--max-classify", type=int, default=50)' in shadow
    veto = (ROOT / "utils" / "event_news_grok_veto.py").read_text(encoding="utf-8")
    assert 'return result == "yes"' in veto


def test_sports_domain_is_excluded_before_local_classification(tmp_path: Path) -> None:
    """A sports countable prop stays sports and countable, and the model never sees it."""
    calls: list[str] = []

    def _open(request: Any, timeout: float = 0) -> Any:
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        prompt = body["messages"][1]["content"]
        calls.append(prompt)
        assert "KXNFLRSH" not in prompt
        return _response(_envelope(VALID))(request)

    sports = _market(
        ticker="KXNFLRSH-26NOV20-A",
        event_ticker="KXNFLRSH-26NOV20",
        series_ticker="KXNFLRSH",
        category="Sports",
        title="Rushing attempts",
        rules_primary="Resolves on the count of rushing attempts.",
    )
    books: list[str] = []

    def _book(ticker: str) -> _Book:
        books.append(ticker)
        return _Book("4.00", "6.00")

    funnel = run_shadow(
        _Pages([_market(), sports], []),
        now=NOW,
        out_dir=tmp_path / "local_triage",
        filters=FILTERS,
        classifier=_classifier(_open),
        sports_prefixes=("KXNFL",),
        orderbook_for=_book,
        market_for=lambda _ticker: None,
    )
    assert len(calls) == 1
    assert "KXTEST-26NOV20-A" in calls[0]
    assert books == ["KXTEST-26NOV20-A", "KXNFLRSH-26NOV20-A"]
    assert funnel["sports_excluded"] == 1
    assert funnel["shortlist"] == 1
    assert funnel["shortlist_before_sports"] == 2
    assert funnel["passed_filters"] == 2
    assert funnel["rejected"] == {}
    assert funnel["local_outcomes"] == {"ok": 1}
    sports_row = json.loads(Path(funnel["paths"]["sports"]).read_text(encoding="utf-8").splitlines()[0])
    assert sports_row["ticker"] == "KXNFLRSH-26NOV20-A"
    assert sports_row["deterministic_domain"] == "sports"
    assert sports_row["deterministic_settlement"] == "countable"
    assert sports_row["deterministic_ambiguity"] == "none"
    assert sports_row["yes_bid_size"] == "4.00"
    assert sports_row["track"] == "sports_shadow"
    classified = Path(funnel["paths"]["classification"]).read_text(encoding="utf-8")
    assert "KXNFLRSH" not in classified


def test_select_pilot_round_robin_keeps_aaa_ahead_of_a_larger_bucket() -> None:
    """One bucket cannot fill the pilot. AAA is seated before later other rows."""

    def _row(ticker: str, **overrides: Any) -> dict[str, Any]:
        row = {
            "ticker": ticker,
            "series_ticker": ticker.split("-", 1)[0],
            "title": "Plain bill",
            "rules_primary": "Resolves when the named bill is signed.",
            "rules_secondary": "",
            "deterministic_domain": "politics",
            "deterministic_settlement": "other",
            "deterministic_ambiguity": "none",
        }
        row.update(overrides)
        return row

    aaa = _row(
        "Z-GAS",
        series_ticker="KXAAAGAS",
        title="AAA regular gas",
        rules_primary="AAA national average gas price",
        deterministic_domain="macro",
    )
    others = [_row("A-1"), _row("A-2"), _row("A-3")]
    picked = select_pilot([*others, aaa], 1)
    assert [row["ticker"] for row in picked] == ["Z-GAS"]
    assert select_pilot(others, 0) == []
    with pytest.raises(ValueError):
        select_pilot(others, -1)


def test_human_accuracy_is_not_called_det_match() -> None:
    """Agreement with the heuristic is det_match_rate. Accuracy uses human labels only."""
    ok = {
        "ticker": "KXAAAGASW-26NOV09-4.0100",
        "series_ticker": "KXAAAGAS",
        "outcome": "ok",
        "domain": "macro",
        "settlement": "other",
        "deterministic_domain": "macro",
        "deterministic_settlement": "other",
        "rules_primary": "AAA average regular gas price",
    }
    malformed = dict(ok, ticker="KXAAAGASW-26NOV16-4.0100", outcome="malformed", settlement="")
    labels = [{"series_prefix": "KXAAAGAS", "human_domain": "macro", "human_settlement": "lookup"}]
    match = det_match_rates([ok])
    assert match["det_match_rate"] == 1
    assert match["det_match_rate_meaning"] == "agreement_not_accuracy"
    scores = human_axis_scores([ok, malformed], labels)
    assert scores["labeled_ok"] == 1
    assert scores["labeled_not_ok"] == [{"ticker": "KXAAAGASW-26NOV16-4.0100", "outcome": "malformed"}]
    assert scores["domain_accuracy"] == 1
    assert scores["settlement_accuracy"] == 0
    assert scores["settlement_confusion"] == {"lookup->other": 1}


def test_schema_gate_keeps_timeouts_out_of_the_valid_rate() -> None:
    """49 of 50 is a miss. A timeout is reported beside the schema rate and is not relabeled."""
    miss = schema_rates([{"outcome": "ok"}] * 49 + [{"outcome": "malformed"}])
    assert miss["schema_valid_rate"] == pytest.approx(0.98)
    assert miss["gate_met"] is False
    hung = schema_rates([{"outcome": "timeout"}])
    assert hung["schema_valid_rate"] is None
    assert hung["timeout_rate"] == 1
    assert hung["gate_met"] is False
    separate = schema_rates([{"outcome": "ok"}] * 99 + [{"outcome": "timeout"}])
    assert separate["schema_valid_rate"] == 1
    assert separate["valid_rate"] == pytest.approx(0.99)
    assert separate["timeout_rate"] == pytest.approx(0.01)
    assert separate["gate_met"] is True
    down = schema_rates([{"outcome": "ok"}] * 99 + [{"outcome": "endpoint_unavailable"}])
    assert down["timeout_rate"] == 0
    assert down["schema_valid_rate"] == pytest.approx(0.99)
    assert down["gate_met"] is True


def test_schema_mode_from_probe_never_selects_json_object() -> None:
    """JSON-only output is not the schema. An unconstrained probe stays unenforced."""
    loose = [
        {"mode": "openai_json_schema", "constrained": False},
        {"mode": "openai_json_object", "constrained": True},
        {"mode": "ollama_native", "constrained": False},
    ]
    assert schema_mode_from_probe(loose) == ("openai_json_schema", False)
    native = [
        {"mode": "openai_json_schema", "constrained": False},
        {"mode": "openai_json_object", "constrained": True},
        {"mode": "ollama_native", "constrained": True},
    ]
    assert schema_mode_from_probe(native) == ("ollama_native", True)


def _probe_client(opener: Any, model: str) -> LocalClassifier:
    return LocalClassifier(
        base_url="http://127.0.0.1:11434/v1",
        model_id=model,
        runtime="ollama",
        timeout_s=1,
        opener=opener,
        clock=lambda: NOW,
    )


def test_hello_probe_is_not_schema_enforcement() -> None:
    """A structured-output request that returns the word hello did not enforce the schema."""
    modes: list[str] = []
    paths: list[str] = []

    def _open(request: Any, timeout: float = 0) -> Any:
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        native = "response_format" not in body
        modes.append("native" if native else body["response_format"]["type"])
        paths.append(urlparse(request.full_url).path)
        if native:
            payload = {"message": {"content": "hello"}}
        else:
            payload = {"choices": [{"message": {"content": "hello"}}]}
        return _response(payload)(request)

    client = _probe_client(_open, "qwen2.5:7b")
    report = client.probe()
    assert modes == ["json_schema", "json_object", "native"]
    assert paths == ["/v1/chat/completions", "/v1/chat/completions", "/api/chat"]
    assert report["schema_enforced"] is False
    assert report["selected_mode"] == "openai_json_schema"
    assert client.schema_enforced is False
    assert [row["constrained"] for row in report["attempts"]] == [False, False, False]
    assert [row["parser_failure"] for row in report["attempts"]] == ["not_json", "not_json", "not_json"]
    assert [row["http_status"] for row in report["attempts"]] == [200, 200, 200]
    dumped = json.dumps(report)
    assert "hello" not in dumped


def test_native_schema_can_enforce_when_json_schema_does_not() -> None:
    """The native format schema is selected only when the hello probe comes back as the schema."""

    def _open(request: Any, timeout: float = 0) -> Any:
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        if "response_format" not in body:
            assert body["format"]["additionalProperties"] is False
            assert "think" not in body
            payload = {"message": {"content": json.dumps(VALID)}}
        elif body["response_format"]["type"] == "json_object":
            payload = {"choices": [{"message": {"content": json.dumps(VALID)}}]}
        else:
            payload = {"choices": [{"message": {"content": "hello"}}]}
        return _response(payload)(request)

    client = _probe_client(_open, "qwen2.5:7b")
    report = client.probe()
    assert report["selected_mode"] == "ollama_native"
    assert report["schema_enforced"] is True
    assert "Factbase" not in json.dumps(report)
    assert client.schema_mode == "ollama_native"


def test_qwen3_native_request_sets_think_false_and_qwen25_does_not() -> None:
    """think false is native qwen3 only. Prompt text is not the schema."""
    seen: list[tuple[str, dict[str, Any]]] = []

    def _open(request: Any, timeout: float = 0) -> Any:
        del timeout
        seen.append((urlparse(request.full_url).path, json.loads(request.data.decode("utf-8"))))
        payload = {"message": {"content": json.dumps(VALID)}}
        return _response(payload)(request)

    qwen3 = _probe_client(_open, "qwen3:14b")
    qwen3.schema_mode = "ollama_native"
    assert qwen3.classify(_market())["outcome"] == "ok"
    qwen25 = _probe_client(_open, "qwen2.5:7b")
    qwen25.schema_mode = "ollama_native"
    assert qwen25.classify(_market())["outcome"] == "ok"
    assert seen[0][0] == "/api/chat"
    assert seen[0][1]["think"] is False
    assert seen[0][1]["stream"] is False
    assert "response_format" not in seen[0][1]
    assert seen[1][0] == "/api/chat"
    assert "think" not in seen[1][1]
    assert "response_format" not in seen[1][1]


def test_compare_model_does_not_replace_the_primary_row(tmp_path: Path) -> None:
    """The comparison pass is a second record. It does not overwrite the primary axes."""

    def _open(request: Any, timeout: float = 0) -> Any:
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        settlement = "lookup" if body["model"] == "qwen3:14b" else "countable"
        payload = dict(VALID, settlement=settlement, rationale="Compared axes.")
        return _response(_envelope(payload))(request)

    funnel = run_shadow(
        _Pages([_market()], []),
        now=NOW,
        out_dir=tmp_path / "local_triage",
        filters=FILTERS,
        classifier=_classifier(_open, model="qwen2.5:7b"),
        sports_prefixes=(),
        orderbook_for=lambda _ticker: _Book("4.00", "6.00"),
        market_for=lambda _ticker: None,
        compare_model="qwen3:14b",
    )
    review = json.loads(Path(funnel["paths"]["review"]).read_text(encoding="utf-8"))
    assert review["rows"][0]["local_settlement"] == "countable"
    assert review["rows"][0]["review_settlement"] == "countable"
    primary = json.loads(Path(funnel["paths"]["classification"]).read_text(encoding="utf-8").splitlines()[0])
    compared = json.loads(
        Path(funnel["paths"]["classification_compare"]).read_text(encoding="utf-8").splitlines()[0]
    )
    assert primary["model_id"] == "qwen2.5:7b"
    assert primary["settlement"] == "countable"
    assert compared["model_id"] == "qwen3:14b"
    assert compared["settlement"] == "lookup"


def test_discover_only_writes_the_pilot_without_a_model_call(tmp_path: Path) -> None:
    """Discovery can select the pilot before any classification HTTP."""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("model called")

    funnel = run_shadow(
        _Pages([_market()], []),
        now=NOW,
        out_dir=tmp_path / "local_triage",
        filters=FILTERS,
        classifier=_classifier(_boom),
        sports_prefixes=(),
        orderbook_for=lambda _ticker: _Book(None, None),
        market_for=lambda _ticker: None,
        max_classify=50,
        send_to_model=False,
    )
    assert funnel["attempted"] == 0
    assert funnel["pilot"] == 1
    assert funnel["sports_excluded"] == 0
    assert funnel["local_outcomes"] == {}
    pilot = json.loads(Path(funnel["paths"]["pilot"]).read_text(encoding="utf-8"))
    assert len(pilot["rows"]) == 1
    assert pilot["rows"][0]["deterministic_settlement"] == "countable"


def test_hand_label_fixture_uses_both_axes() -> None:
    """Human labels stay inside the schema. Sports countable and AAA lookup are both present."""
    rows = load_human_labels()
    assert rows
    for row in rows:
        assert row["human_domain"] in DOMAINS
        assert row["human_settlement"] in SETTLEMENTS
    by_prefix = {str(row["series_prefix"]): row for row in rows}
    assert by_prefix["KXAAAGAS"]["human_domain"] == "macro"
    assert by_prefix["KXAAAGAS"]["human_settlement"] == "lookup"
    assert by_prefix["KXNFLRSH"]["human_domain"] == "sports"
    assert by_prefix["KXNFLRSH"]["human_settlement"] == "countable"
    assert by_prefix["KXTRUMPACT"]["human_settlement"] == "ambiguous"
    assert "probability" not in json.dumps(rows)
