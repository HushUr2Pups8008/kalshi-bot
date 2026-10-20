"""Politics desk flags default on for paper and off for live."""

from __future__ import annotations

from utils.event_news_desk_flags import (
    EVENT_NEWS_MAX_RISK,
    EVENT_NEWS_ONE_PER_FAMILY,
    EVENT_NEWS_YES_FLOOR,
    event_news_desk_flag_enabled,
    event_news_desk_flags_boot_suffix,
    event_news_exposure_skip_reason,
    event_news_family_skip_reason,
    event_news_maker_limit_cents,
    event_news_risk_capped_contracts,
    event_news_yes_floor_skip_reason,
)
from utils.event_news_research import EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN


def test_missing_env_is_paper_on_and_live_off() -> None:
    assert event_news_desk_flag_enabled(EVENT_NEWS_YES_FLOOR, live=False, environ={}) is True
    assert event_news_desk_flag_enabled(EVENT_NEWS_YES_FLOOR, live=True, environ={}) is False
    assert event_news_desk_flag_enabled(
        EVENT_NEWS_YES_FLOOR, live=True, environ={EVENT_NEWS_YES_FLOOR: "true"}
    ) is True
    assert event_news_desk_flag_enabled(
        EVENT_NEWS_YES_FLOOR, live=False, environ={EVENT_NEWS_YES_FLOOR: "false"}
    ) is False
    assert event_news_desk_flag_enabled(
        EVENT_NEWS_YES_FLOOR, live=False, environ={EVENT_NEWS_YES_FLOOR: "no"}
    ) is False


def test_yes_floor_zero_is_off_and_a_positive_cent_value_is_on() -> None:
    off = {EVENT_NEWS_YES_FLOOR: "0"}
    on = {EVENT_NEWS_YES_FLOOR: "55"}
    assert event_news_desk_flag_enabled(EVENT_NEWS_YES_FLOOR, live=True, environ=off) is False
    assert event_news_desk_flag_enabled(EVENT_NEWS_YES_FLOOR, live=False, environ=off) is False
    assert event_news_yes_floor_skip_reason("yes", 36, live=False, environ=off) is None
    assert event_news_desk_flag_enabled(EVENT_NEWS_YES_FLOOR, live=True, environ=on) is True
    assert event_news_yes_floor_skip_reason("yes", 54, live=True, environ=on) == "yes_below_floor"


def test_yes_and_on_enable_a_desk_flag_and_other_words_do_not() -> None:
    assert event_news_desk_flag_enabled(
        EVENT_NEWS_YES_FLOOR, live=True, environ={EVENT_NEWS_YES_FLOOR: "yes"}
    ) is True
    assert event_news_desk_flag_enabled(
        EVENT_NEWS_YES_FLOOR, live=True, environ={EVENT_NEWS_YES_FLOOR: "on"}
    ) is True
    assert event_news_desk_flag_enabled(
        EVENT_NEWS_YES_FLOOR, live=False, environ={EVENT_NEWS_YES_FLOOR: "maybe"}
    ) is False


def test_yes_floor_drops_yes_below_55_and_keeps_no_at_36() -> None:
    assert event_news_yes_floor_skip_reason("yes", 54, live=False, environ={}) == "yes_below_floor"
    assert event_news_yes_floor_skip_reason("yes", 55, live=False, environ={}) is None
    assert event_news_yes_floor_skip_reason("no", 36, live=False, environ={}) is None
    assert event_news_yes_floor_skip_reason("yes", 36, live=True, environ={}) is None
    assert event_news_yes_floor_skip_reason("yes", "36", live=False, environ={}) == "yes_below_floor"


def test_boot_suffix_prints_live_off_and_paper_on() -> None:
    line = event_news_desk_flags_boot_suffix({})
    assert "YES_FLOOR live=off paper=on cents=55" in line
    assert "RISK_CAP live=off paper=on cents=600" in line
    assert "MAKER_FIRST live=off paper=on wait_s=180" in line
    assert "RULES_CHECK live=off paper=on" in line
    assert "EDGE_SCAN live=off paper=on" in line
    assert "EXIT_WATCH live=off paper=on" in line
    assert "SESSION_HALT live=off paper=on" in line


def test_risk_cap_example_89c_uses_the_600_cent_paper_default() -> None:
    # 600 // 89 is 6. Live stays uncapped by this flag when the env is unset.
    assert event_news_risk_capped_contracts(8, 89, live=False, environ={}) == 6
    assert event_news_risk_capped_contracts(22, 33, live=False, environ={}) == 8
    assert event_news_risk_capped_contracts(8, 89, live=True, environ={}) == 8
    assert event_news_risk_capped_contracts(8, 0, live=False, environ={}) == 0


def test_one_per_family_blocks_a_second_open_row() -> None:
    reason = event_news_family_skip_reason(
        "KXTRUMPACT-26OCT12-T1",
        ["KXTRUMPACT-26OCT11-T5"],
        live=False,
        environ={},
    )
    assert reason == "one_per_family"
    assert (
        event_news_family_skip_reason(
            "KXTRUMPSAY-26OCT19-GOLF",
            ["KXTRUMPACT-26OCT11-T5"],
            live=False,
            environ={},
        )
        is None
    )
    assert (
        event_news_family_skip_reason(
            "KXTRUMPACT-26OCT12-T1",
            ["KXTRUMPACT-26OCT11-T5"],
            live=True,
            environ={},
        )
        is None
    )
    assert (
        event_news_family_skip_reason(
            "KXTRUMPSAY-26OCT20-X",
            [EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN],
            live=False,
            environ={EVENT_NEWS_ONE_PER_FAMILY: "true"},
        )
        == "one_per_family"
    )


def test_failed_universe_one_per_family_stays_closed(tmp_path) -> None:
    """Paper defaults the flag on. Live stays off when the env is unset."""
    from utils.series_universe import reset_series_universe

    reset_series_universe(tmp_path / "missing.yaml")
    try:
        assert (
            event_news_family_skip_reason(
                "KXTRUTHSOCIAL-26OCT10-B200",
                [],
                live=False,
                environ={},
            )
            == "one_per_family"
        )
        assert (
            event_news_family_skip_reason(
                "KXTRUTHSOCIAL-26OCT10-B200",
                [],
                live=True,
                environ={},
            )
            is None
        )
    finally:
        reset_series_universe()


def test_exposure_blocks_when_open_cost_exceeds_a_quarter_of_equity() -> None:
    """Today's book is 1872 cents against 1160 cents of room on 4640 equity."""
    assert (
        event_news_exposure_skip_reason(1872, 100, 4640, live=False, environ={})
        == "exposure_cap"
    )
    assert event_news_exposure_skip_reason(100, 100, 4640, live=False, environ={}) is None
    assert event_news_exposure_skip_reason(1872, 100, 4640, live=True, environ={}) is None
    assert event_news_exposure_skip_reason(-1, 100, 4640, live=False, environ={}) == "exposure_cap"


def test_maker_price_is_one_cent_better_and_stops_at_the_band() -> None:
    assert event_news_maker_limit_cents(89, floor_cents=55) == 88
    assert event_news_maker_limit_cents(36, floor_cents=36) is None
    assert event_news_maker_limit_cents(55, floor_cents=55) is None
    assert event_news_risk_capped_contracts(8, 89, live=False, environ={EVENT_NEWS_MAX_RISK: "400"}) == 4
