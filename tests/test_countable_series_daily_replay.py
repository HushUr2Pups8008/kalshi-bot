"""Daily count replay. No network, no Grok, no orders."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts.countable_series_daily_replay import Stop, decision_line, score_days

ET = ZoneInfo("America/New_York")
DAY = datetime(2026, 10, 3, tzinfo=ET)


def _posts() -> list[datetime]:
    found = [(DAY - timedelta(days=offset)).replace(hour=12) for offset in range(1, 7)]
    found.extend(DAY.replace(hour=hour) for hour in range(1, 18))
    return found


def _candle(moment: datetime, ask: str, bid: str) -> dict:
    return {
        "end_period_ts": int(moment.timestamp()),
        "yes_ask": {"close_dollars": ask},
        "yes_bid": {"close_dollars": bid},
    }


def _market(ticker: str, rules: str, result: str) -> dict:
    return {
        "ticker": ticker,
        "rules_primary": rules,
        "strike_type": "between",
        "result": result,
    }


def test_settled_day_uses_the_count_and_a_disagreement_stops() -> None:
    """Seventeen posts make 15-19 yes and 20-24 no. A swapped result stops the table."""
    rules_15 = (
        "Resolves Yes if the number of Truth Social posts by Donald Trump "
        "on Oct 3, 2026 is between 15 and 19."
    )
    rules_20 = (
        "Resolves Yes if the number of Truth Social posts by Donald Trump "
        "on Oct 3, 2026 is between 20 and 24."
    )
    noon = DAY.replace(hour=12)
    candles = {
        "KXTRUTHSOCIALD-26OCT03-B17": [_candle(DAY, "0.4000", "0.3200"), _candle(noon, "0.5500", "0.4700")],
        "KXTRUTHSOCIALD-26OCT03-B22": [_candle(DAY, "0.3000", "0.2200"), _candle(noon, "0.2500", "0.1700")],
    }

    def candles_of(ticker: str, _start: int, _end: int) -> list[dict]:
        return candles[ticker]

    scored = score_days(
        _posts(),
        [
            _market("KXTRUTHSOCIALD-26OCT03-B17", rules_15, "yes"),
            _market("KXTRUTHSOCIALD-26OCT03-B22", rules_20, "no"),
        ],
        candles_of,
        capped=False,
    )
    assert scored["settled_days"] == [DAY.date()]
    opened = [row for row in scored["rows"]["day_open"] if not row["missing"]]
    assert {row["label"] for row in opened} == {"15-19", "20-24"}
    assert {row["label"]: row["outcome"] for row in opened} == {"15-19": 1, "20-24": 0}
    assert all(row["p"] is not None for row in opened)
    assert decision_line(scored["rows"]["day_open"]).endswith("clearly_negative=no")
    with pytest.raises(Stop, match="result_mismatch"):
        score_days(
            _posts(),
            [_market("KXTRUTHSOCIALD-26OCT03-B17", rules_15, "no")],
            candles_of,
            capped=False,
        )


def test_an_open_day_is_not_a_settled_sample() -> None:
    rules = "Resolves Yes if the number of Truth Social posts by Donald Trump on Oct 4, 2026 is below 10."
    scored = score_days(
        _posts(),
        [{
            "ticker": "KXTRUTHSOCIALD-26OCT04-T10",
            "rules_primary": rules,
            "strike_type": "",
            "result": "",
        }],
        lambda _ticker, _start, _end: [],
        capped=False,
    )
    assert scored["settled_days"] == []
    assert scored["open_days"] == [datetime(2026, 10, 4, tzinfo=ET).date()]
    assert decision_line([]) == "paper_decision clock=day_open realized_sum=0.000000 n=0 clearly_negative=no"
