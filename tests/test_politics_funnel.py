"""The 14-day funnel counts drops and does not print order ids."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal

from scripts.politics_funnel import (
    build_funnel,
    format_funnel,
    largest_positive_removal,
    window_bounds,
)
from scripts.simulations.politics_desk_replay import replay_modes


def test_window_is_fourteen_utc_days() -> None:
    start, end = window_bounds(date(2026, 10, 13))
    assert start == datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert (end - start).days == 14


def test_missing_log_and_database_do_not_fail(tmp_path) -> None:
    report = build_funnel(
        report_day=date(2026, 10, 13),
        log_path=tmp_path / "missing.log",
        db_path=tmp_path / "missing.db",
    )
    assert report["log"]["candidates_lines"] == "not_measured"
    assert report["paper"]["n"] == "not_measured"
    text = format_funnel(report)
    assert "not_measured" in text
    assert "risk_cap" in text


def test_fixture_counts_only_lines_inside_the_window(tmp_path) -> None:
    log_path = tmp_path / "politics.log"
    log_path.write_text(
        "\n".join(
            [
                "2026-09-29 23:59:59,000 UTC INFO [EVENT_NEWS_PREWARM] ticker=KXOLD",
                "2026-09-30 00:00:01,000 UTC INFO [EVENT_NEWS_PREWARM] ticker=KXTRUTHSOCIAL-26OCT03-B129",
                "2026-10-02 01:00:00,000 UTC INFO [EVENT_NEWS_RISK] executed_ask_in_excluded_band ticker=KXTRUTHSOCIAL-26OCT03-B129",
                "2026-10-02 01:00:01,000 UTC INFO [EVENT_NEWS_RISK] yes_below_floor ticker=KXTRUMPSAY-26OCT19-GOLF",
                "2026-10-02 01:00:02,000 UTC INFO [EVENT_NEWS_RISK_CAP] ticker=KXTRUMPACT-26OCT04-T6 ask=89 contracts=0",
                "2026-10-02 01:00:03,000 UTC INFO [EVENT_NEWS_RISK_CAP] ticker=KXTRUMPACT-26OCT04-T6 ask=89 contracts=8 capped=4",
                "2026-10-02 01:00:04,000 UTC INFO [EVENT_NEWS_RISK] exposure_cap ticker=KXTRUMPACT-26OCT11-T5",
                "2026-10-02 01:00:05,000 UTC INFO [EVENT_NEWS_ADMIT] blocked reason=event_open_prefix_cap open=KXTRUMPACT-26OCT11-T5 blocked=KXTRUMPACT-26OCT04-T6",
                "2026-10-02 01:00:06,000 UTC INFO [EVENT_NEWS_FAMILY_LOCK] open=KXTRUTHSOCIAL-26OCT17-B129 blocked=KXTRUTHSOCIAL-26OCT03-B129",
                "2026-10-02 01:00:07,000 UTC INFO [LIVE] BLOCKED KXTRUMPSAY-26OCT19-GOLF: EVENT_NEWS_GROK_VETO did not return yes",
                "2026-10-02 01:00:08,000 UTC INFO [EVENT_NEWS_GROK_WATCH] result=contradict ticker=KXTRUMPSAY-26OCT19-GOLF side=yes",
                "2026-10-02 01:00:09,000 UTC INFO [DECISION] skip ticker=KXTRUMPSAY-26SEP14-UKRA reason=edge +0.0100 below min_edge 0.0400",
                "2026-10-02 01:00:10,000 UTC INFO [LIVE_GUARD] BLOCKED live order for KXTRUMPSAY-26OCT19-GOLF: unknown submission hold",
                "2026-10-02 01:00:11,000 UTC INFO client_order_id=secret-order ghp_token api_key=secret signal_headline=hidden",
                "2026-10-14 00:00:00,000 UTC INFO [EVENT_NEWS_PREWARM] ticker=KXAFTER",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "paper.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE paper_trades (ts TEXT, resolved INT, gross_payout_cents INT, cost_dollars REAL, signal_headline TEXT)"
    )
    connection.execute(
        "INSERT INTO paper_trades VALUES (?, 1, 0, 7.5226, 'do not print')",
        ("2026-10-03T00:00:00+00:00",),
    )
    connection.execute(
        "INSERT INTO paper_trades VALUES (?, 1, 800, 6.0, 'outside')",
        ("2026-08-29T00:00:00+00:00",),
    )
    connection.commit()
    connection.close()
    report = build_funnel(
        report_day=date(2026, 10, 13),
        log_path=log_path,
        db_path=db_path,
    )
    log = report["log"]
    assert log["candidates_lines"] == 1
    assert log["candidates_tickers"] == 1
    stages = log["stages"]
    assert stages["band_guard"]["lines"] == 1
    assert stages["yes_floor"]["lines"] == 1
    assert stages["risk_cap"]["lines"] == 1
    assert stages["exposure"]["lines"] == 1
    assert stages["family"]["lines"] == 2
    assert stages["family"]["tickers"] == 2
    assert stages["veto"]["lines"] == 1
    assert stages["watch"]["lines"] == 1
    assert stages["edge"]["lines"] == 1
    assert log["hold_lines"] == 1
    assert report["paper"]["n"] == 1
    assert report["paper"]["fee_net"] == "-7.5226"
    text = format_funnel(report)
    assert "secret-order" not in text
    assert "ghp_" not in text
    assert "api_key" not in text
    assert "signal_headline" not in text
    assert "do not print" not in text
    assert "KXAFTER" not in text


def test_window_fee_net_and_the_risk_cap_removes_the_most_good_trades() -> None:
    report = build_funnel(
        report_day=date(2026, 10, 13),
        log_path=__import__("pathlib").Path("/no/such/politics.log"),
        db_path=__import__("pathlib").Path("/no/such/paper.db"),
    )
    entries = report["entries"]
    assert entries["live_settled_n"] == 3
    assert entries["live_open_n"] == 3
    assert entries["live_n"] == 6
    assert entries["live_fee_net"] == "-1.8144"
    assert entries["replay_paper_n"] == 0
    assert entries["yes_floor_positive_removed"] == "0.0000"
    assert entries["yes_floor_n"] == 2
    assert Decimal(entries["yes_floor_fee_net"]) > 0
    assert Decimal(entries["risk_cap_positive_removed"]) > 0
    assert entries["family_fee_net"] == "not_measured"
    assert entries["exposure_fee_net"] == "not_measured"
    mode, removed = largest_positive_removal(replay_modes())
    assert mode == "risk_cap"
    assert removed == "4.3998"
    text = format_funnel(report)
    assert "The filter that removes the most positive fee-net is risk_cap (4.3998)." in text
