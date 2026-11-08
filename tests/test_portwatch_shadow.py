"""PortWatch paper baseline. A revised file is not a holdout and not a fee."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils.portwatch_shadow import (
    PAGE_ROWS,
    PAPER_CONTRACTS,
    _ManualRedirect,
    fetch_allowlisted,
    forecast_contracts,
    parse_contract,
    query_url,
    record_shadow,
    require_allowlisted,
    same_vintage_holdout,
    score_forward,
)

ROOT = Path(__file__).resolve().parents[1]
SUEZ = "chokepoint1"
NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
TARGET = (
    "If the number of transit calls through the Suez Canal as reported by the IMF PortWatch "
    "is above {strike} from Nov 9, 2026 to Nov 15, 2026, then the market resolves to Yes."
)
SECONDARY = (
    "The total is calculated by summing daily counts from Nov 9, 2026 to Nov 15, 2026. "
    "The market will resolve no earlier than Tuesday at 9 AM following the end of the period, "
    "once complete data is available."
)


def _contract(strike: int, series: str = "KXSUEZWEEKLY") -> dict[str, str]:
    return {"series_ticker": series, "rules_primary": TARGET.format(strike=strike), "rules_secondary": SECONDARY}


def _feature(day: date, total: int, portid: str = SUEZ, name: str = "Suez Canal") -> dict[str, object]:
    return {
        "attributes": {
            "date": day.isoformat(),
            "portid": portid,
            "portname": name,
            "n_total": total,
            "n_container": total,
            "n_dry_bulk": 0,
            "n_general_cargo": 0,
            "n_roro": 0,
            "n_tanker": 0,
            "n_cargo": total,
        }
    }


def _weeks(totals: list[int], end: date = date(2026, 11, 8)) -> list[dict[str, object]]:
    """Newest week ends on ``end``. The first list entry is the oldest week."""
    rows: list[dict[str, object]] = []
    start = end - timedelta(days=7 * len(totals) - 1)
    for index, total in enumerate(totals):
        week = start + timedelta(days=7 * index)
        for offset in range(7):
            rows.append(_feature(week + timedelta(days=offset), total if offset == 0 else 0))
    return rows


def _layer(when: datetime) -> bytes:
    stamp = int((when - timedelta(days=1)).timestamp() * 1000)
    return json.dumps({"editingInfo": {"dataLastEditDate": stamp}}).encode("utf-8")


def _transport(rows: list[dict[str, object]], when: datetime, page: int | None = None):
    calls: list[str] = []

    def transport(url: str) -> bytes:
        require_allowlisted(url)
        calls.append(url)
        parts = url.split("?", 1)
        if "/query?" not in url:
            return _layer(when)
        query = parts[1]
        offset = int(query.split("resultOffset=")[1].split("&")[0])
        size = page or PAGE_ROWS
        chunk = rows[offset:offset + size]
        exceeded = offset + size < len(rows)
        return json.dumps({"features": chunk, "exceededTransferLimit": exceeded}).encode("utf-8")

    return transport, calls


def _suez(row: dict[str, object]) -> dict[str, object]:
    forecasts = row["forecasts"]
    assert isinstance(forecasts, list)
    found = [item for item in forecasts if item["series_ticker"] == "KXSUEZWEEKLY"]
    assert len(found) == 1
    return found[0]


def test_private_and_model_urls_are_not_fetched() -> None:
    """A drafted URL is not a fetch instruction. Private targets never open a socket."""

    def opener(request, timeout=0):
        raise AssertionError((request.full_url, timeout))

    rejected = (
        "http://127.0.0.1/latest",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/query",
        "https://[::1]/query",
        "file:///etc/passwd",
        "https://user:pass@services9.arcgis.com/secret",
        "https://services9.arcgis.com:8443/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0?f=json",
        "http://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0?f=json",
        "https://example.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query?f=json",
        query_url(SUEZ).replace("chokepoint1", "chokepoint2"),
    )
    for url in rejected:
        with pytest.raises(ValueError):
            fetch_allowlisted(url, urlopen=opener)
    with pytest.raises(ValueError):
        query_url("chokepoint2")


def test_redirect_off_the_allowlist_is_not_followed() -> None:
    """One hop is allowed only when the next URL is still the official query."""
    calls: list[str] = []

    class Response:
        def __init__(self, status: int, headers: dict[str, str], body: bytes = b"{}") -> None:
            self.status = status
            self.headers = headers
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

    def opener(request, timeout=0):
        del timeout
        calls.append(request.full_url)
        if len(calls) == 1:
            return Response(302, {"Location": "https://evil.example/collect"})
        raise AssertionError("followed")

    with pytest.raises(ValueError):
        fetch_allowlisted(query_url(SUEZ), urlopen=opener)
    assert len(calls) == 1

    allowed = query_url("chokepoint4", 0)

    def follow(request, timeout=0):
        del timeout
        if request.full_url == query_url(SUEZ):
            return Response(302, {"Location": allowed})
        return Response(200, {}, b'{"features":[]}')

    assert fetch_allowlisted(query_url(SUEZ), urlopen=follow) == b'{"features":[]}'
    sentinel = object()
    assert _ManualRedirect().http_error_302(None, sentinel, 302, "found", {}) is sentinel


def test_registry_promotion_is_not_source_or_estimator_support(tmp_path: Path) -> None:
    """An empty official file is still a miss. The registry row does not fill it."""
    transport, calls = _transport([], NOW)
    row = record_shadow(now=NOW, out_dir=tmp_path, contracts=[_contract(310)], transport=transport)
    forecast = _suez(row)
    assert forecast["registry_qualified"] is True
    assert forecast["source_supported"] is False
    assert forecast["estimator_supported"] is False
    assert forecast["point_forecast"] is None
    assert row["publication_time"] is None
    assert row["publication_time"] != row["retrieval_time"]
    assert row["fee_net"] == "not_estimable"
    assert row["market_comparison"] == "not_estimable"
    assert row["independent_settled_events"] == 0
    assert "probability" not in json.dumps(row)
    assert all("chokepoint2" not in url for url in calls)
    stored = json.loads((tmp_path / "snapshots.jsonl").read_text(encoding="utf-8"))
    assert stored["publication_time"] is None
    digest = row["layer"][0]["sha256"]
    raw = (tmp_path / row["layer"][0]["raw_path"]).read_bytes()
    assert __import__("hashlib").sha256(raw).hexdigest() == digest


def test_cutoff_does_not_zero_fill_or_use_the_decision_date(tmp_path: Path) -> None:
    """A day still in progress is not part of the sum, and a missing day is not zero."""
    decision = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
    rows = [_feature(date(2026, 11, 2) + timedelta(days=offset), 100 if offset == 6 else 1) for offset in range(7)]
    transport, _calls = _transport(rows, decision)
    row = record_shadow(now=decision, out_dir=tmp_path, contracts=[_contract(310)], transport=transport)
    forecast = _suez(row)
    assert forecast["source_supported"] is True
    assert forecast["skip_reason"] == "prior_week_incomplete"
    assert forecast["point_forecast"] is None

    midnight = datetime(2026, 11, 9, 0, tzinfo=timezone.utc)
    transport, _calls = _transport(rows, midnight)
    at_midnight = record_shadow(now=midnight, out_dir=tmp_path, contracts=[_contract(310)], transport=transport)
    assert _suez(at_midnight)["point_forecast"] is None

    opened = midnight + timedelta(seconds=1)
    complete = [_feature(date(2026, 11, 2) + timedelta(days=offset), 1) for offset in range(7)]
    transport, _calls = _transport(complete, opened)
    after = record_shadow(now=opened, out_dir=tmp_path, contracts=[_contract(310)], transport=transport)
    assert _suez(after)["point_forecast"] == 7


def test_eight_week_dispersion_is_not_a_shorter_sample(tmp_path: Path) -> None:
    """Eight aligned weeks set the dispersion. Seven weeks leave it unknown."""
    totals = [2, 4, 4, 4, 5, 5, 7, 9]
    transport, calls = _transport(_weeks(totals), NOW, page=20)
    row = record_shadow(now=NOW, out_dir=tmp_path, contracts=[_contract(10), _contract(20)], transport=transport)
    forecast = _suez(row)
    assert forecast["strike_count"] == 2
    assert forecast["strikes"] == [10, 20]
    assert forecast["point_forecast"] == 9
    assert forecast["uncertainty"] == pytest.approx(math.sqrt(32 / 7))
    assert forecast["uncertainty_label"] == "current_vintage_dispersion"
    assert forecast["estimator_supported"] is False
    assert same_vintage_holdout(row)["reason"] == "revised_vintage_not_asof"
    assert any("resultOffset=20" in url for url in calls)

    short, _calls = _transport(_weeks(totals[1:]), NOW)
    shorter = record_shadow(now=NOW, out_dir=tmp_path, contracts=[_contract(10)], transport=short)
    assert _suez(shorter)["point_forecast"] == 9
    assert _suez(shorter)["uncertainty"] is None
    assert _suez(shorter)["uncertainty_reason"] == "fewer_than_8_complete_weeks"


def test_same_vintage_history_and_early_decision_are_not_a_backtest(tmp_path: Path) -> None:
    """The current file cannot stand in for what was published at an earlier clock."""
    rows = _weeks([9]) + [_feature(date(2026, 11, 9) + timedelta(days=offset), 3) for offset in range(7)]
    transport, _calls = _transport(rows, NOW)
    published = record_shadow(now=NOW, out_dir=tmp_path, contracts=[_contract(10)], transport=transport)
    assert _suez(published)["skip_reason"] == "window_already_published"
    assert _suez(published)["point_forecast"] is None

    early = NOW - timedelta(days=30)
    refused = record_shadow(
        now=NOW,
        out_dir=tmp_path,
        contracts=[_contract(10)],
        transport=_transport(_weeks([2, 4, 4, 4, 5, 5, 7, 9]), NOW)[0],
        decision_time=early,
    )
    assert _suez(refused)["skip_reason"] == "decision_not_retrieval"
    assert _suez(refused)["point_forecast"] is None


def test_forward_score_needs_a_later_vintage_and_still_has_no_fee() -> None:
    """A second edit can score one event. Sibling strikes do not multiply the sample."""
    prior = {
        "retrieval_time": "2026-11-08T12:00:00.000Z",
        "table_vintage_time": "2026-11-07T14:59:47.722Z",
        "forecasts": [
            {
                "event_key": "KXSUEZWEEKLY:2026-11-02:2026-11-08",
                "portid": SUEZ,
                "start": "2026-11-02",
                "end": "2026-11-08",
                "point_forecast": 70,
                "source_supported": True,
                "strikes": [60, 80],
            }
        ],
    }
    later = {
        "retrieval_time": "2026-11-11T15:00:00.000Z",
        "table_vintage_time": "2026-11-10T14:00:00.000Z",
    }
    prior_rows = {SUEZ: {date(2026, 10, 26) + timedelta(days=offset): 10 for offset in range(7)}}
    later_rows = {
        SUEZ: {
            **prior_rows[SUEZ],
            **{date(2026, 11, 2) + timedelta(days=offset): 11 for offset in range(7)},
        }
    }
    scored = score_forward(prior, later, prior_rows=prior_rows, later_rows=later_rows)
    assert scored["evaluation"] == "forward_first_complete_vintage"
    assert scored["independent_settled_events"] == 1
    assert scored["events"][0]["absolute_error"] == 7
    assert scored["events"][0]["strike_count"] == 2
    assert scored["estimator_supported"] is True
    assert scored["fee_net"] == "not_estimable"
    assert scored["market_comparison"] == "not_estimable"
    assert scored["publication_time"] is None

    leaked = score_forward(prior, later, prior_rows=later_rows, later_rows=later_rows)
    assert leaked["evaluation"] == "not_yet_evaluable"
    assert leaked["reason"] == "revised_vintage_not_asof"
    assert leaked["independent_settled_events"] == 0
    assert leaked["estimator_supported"] is False


def test_conflict_truncation_and_max_contract_skip(tmp_path: Path) -> None:
    """Conflicting counts, a capped download, and the max contract do not become a sum."""
    bad = _feature(date(2026, 11, 1), 5)
    bad["attributes"]["n_container"] = 4
    transport, _calls = _transport([bad], NOW)
    conflict = record_shadow(now=NOW, out_dir=tmp_path, contracts=[_contract(1)], transport=transport)
    assert _suez(conflict)["source_supported"] is False
    assert _suez(conflict)["skip_reason"] == "conflicting_counts"

    def always_short(url: str) -> bytes:
        require_allowlisted(url)
        if "/query?" not in url:
            return _layer(NOW)
        return json.dumps(
            {"features": [_feature(date(2026, 11, 1), 1)], "exceededTransferLimit": True}
        ).encode("utf-8")

    truncated = record_shadow(now=NOW, out_dir=tmp_path, contracts=[_contract(1)], transport=always_short)
    assert _suez(truncated)["source_supported"] is False
    assert _suez(truncated)["skip_reason"] == "truncated"
    assert _suez(truncated)["point_forecast"] is None

    hormuz = next(item for item in PAPER_CONTRACTS if item["series_ticker"] == "KXHORMUZMAX")
    parsed = parse_contract(hormuz["series_ticker"], hormuz["rules_primary"], hormuz["rules_secondary"])
    assert parsed is not None and parsed["how"] == "max_day"
    ports = {"chokepoint6": {"source_supported": True, "skip_reason": "", "portid": "chokepoint6", "rows": {}}}
    forecast = forecast_contracts([hormuz], ports, decision=NOW, retrieval=NOW, vintage=NOW - timedelta(days=1))
    assert forecast[0]["registry_qualified"] is True
    assert forecast[0]["skip_reason"] == "aggregation_not_weekly_sum"
    assert forecast[0]["estimator_supported"] is False
    panama_rules = TARGET.format(strike=220).replace("Suez Canal", "Panama Canal")
    panama = parse_contract("KXPANAMAWEEKLY", panama_rules, SECONDARY)
    assert panama is not None
    assert forecast_contracts(
        [{"series_ticker": "KXPANAMAWEEKLY", "rules_primary": panama_rules, "rules_secondary": SECONDARY}],
        {},
        decision=NOW,
        retrieval=NOW,
        vintage=NOW,
    )[0]["skip_reason"] == "not_registry_qualified"


def test_paper_contracts_match_the_weekly_rules_and_stay_out_of_live_paths() -> None:
    """The captured sentences are weekly sums. Live modules do not import this snapshot."""
    kinds = []
    for contract in PAPER_CONTRACTS:
        parsed = parse_contract(contract["series_ticker"], contract["rules_primary"], contract["rules_secondary"])
        assert parsed is not None
        kinds.append(parsed["how"])
        assert "IMF PortWatch" in contract["rules_primary"]
    assert kinds.count("weekly_sum") == 6
    assert kinds.count("max_day") == 1
    source = (ROOT / "utils" / "portwatch_shadow.py").read_text(encoding="utf-8")
    assert "place_limit_order" not in source
    assert "api.x.ai" not in source
    for folder in ("trading", "tasks", "feeds", "analysis"):
        for path in (ROOT / folder).rglob("*.py"):
            assert "portwatch_shadow" not in path.read_text(encoding="utf-8")
    assert "portwatch_shadow" not in (ROOT / "main.py").read_text(encoding="utf-8")
