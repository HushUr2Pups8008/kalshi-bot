"""Fee-net P&L stays on the exchange fills and never prints an order id."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from scripts.pnl_report import (
    assemble_pnl,
    collect_pages,
    incomplete_report,
    replace_if_unchanged,
    run_report,
)

WHEN = "2026-10-17T03:09:47Z"
UUID_TEXT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def _buy(**overrides):
    row = {
        "ticker": "KXTRUMPSAY-26OCT19-ZACH",
        "side": "yes",
        "action": "buy",
        "count_fp": "8.00",
        "yes_price_dollars": "0.6400",
        "no_price_dollars": "0.3600",
        "fee_cost": "0.129100",
        "created_time": WHEN,
        "order_id": "fill-order-1",
    }
    row.update(overrides)
    return row


def _settlement(**overrides):
    row = {
        "ticker": "KXTRUMPSAY-26OCT19-ZACH",
        "market_result": "yes",
        "yes_count_fp": "8.00",
        "no_count_fp": "0.00",
        "yes_total_cost_dollars": "5.1200",
        "no_total_cost_dollars": "0.0000",
        "revenue": 800,
        "fee_cost": "0.1291",
        "settled_time": "2026-10-19T00:00:00Z",
    }
    row.update(overrides)
    return row


def _log_line() -> str:
    return (
        "2026-10-17T03:09:47Z, KXTRUMPSAY-26OCT19-ZACH, yes, 64, 8, "
        f"{UUID_TEXT}, 201, 8, 64, 12.91, \n"
    )


def test_yes_win_subtracts_the_fill_fee_once_and_hides_ids():
    """8 YES at 64c that settle YES pay $8. The fee is 0.1291, once.

    Premium is 5.12. Fee-net is 8 - 5.12 - 0.1291 = 2.7509. An equal
    settlement fee must not be subtracted a second time.
    """
    report, new_log, tickers = assemble_pnl(
        fill_rows=[_buy()],
        settlement_rows=[_settlement()],
        fill_log_text=_log_line(),
        paper_rows=[
            {
                "ticker": "KXTRUTHSOCIAL-26AUG29-T240",
                "side": "yes",
                "contracts": 8,
                "price_cents": 85,
                "resolved": 1,
                "pnl_dollars": 100,
                "series_ticker": "",
                "signal_type": "research_decision_grade",
            }
        ],
        open_tickers=set(),
        now=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    assert "running_total_dollars: 2.7509" in report
    assert "2.6218" not in report
    assert "| KXTRUMPSAY | 2.7509 |" in report
    assert "KXTRUTHSOCIAL-26AUG29-T240" in report
    assert "Not included in the live fee-net total." in report
    assert "order_id" not in report
    assert UUID_TEXT not in report
    assert "fill-order-1" not in report
    assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", report)
    assert tickers == ["KXTRUMPSAY-26OCT19-ZACH"]
    assert "fill-order-1" in new_log
    assert "backfilled_rows: KXTRUMPSAY-26OCT19-ZACH" in report


def test_loss_open_sell_scalar_and_fee_mismatch_stay_fail_closed():
    loss = _buy(ticker="KXTRUMPSAY-26OCT19-MELA")
    open_fill = _buy(
        ticker="KXTRUMPSAY-26OCT26-GOLF",
        yes_price_dollars="0.8900",
        fee_cost="0.054900",
        order_id="fill-order-2",
    )
    sell = _buy(ticker="KXTRUMPACT-26OCT04-T6", action="sell", side="no", no_price_dollars="0.4000")
    missing = _buy(ticker="KXTRUMPACT-26OCT11-T5", action="")
    scalar = _buy(ticker="KXTRUTHSOCIAL-26OCT03-B129", yes_price_dollars="0.5000", fee_cost="0.0100")
    old_log = (
        "2026-10-03T00:00:00Z, KXTRUTHSOCIAL-26OCT10-B149, yes, 40, 8, old-client, 201\n"
    )
    report, _new_log, tickers = assemble_pnl(
        fill_rows=[
            _buy(fee_cost="0.1291"),
            loss,
            open_fill,
            sell,
            missing,
            scalar,
            _buy(ticker="KXTRUTHSOCIAL-26OCT10-B149", yes_price_dollars="0.4000", fee_cost="0.0800", order_id=None),
        ],
        settlement_rows=[
            _settlement(),
            _settlement(ticker="KXTRUMPSAY-26OCT19-MELA", market_result="no", revenue=0),
            _settlement(ticker="KXTRUTHSOCIAL-26OCT03-B129", market_result="scalar", revenue=None, fee_cost="0"),
            _settlement(
                ticker="KXTRUTHSOCIAL-26OCT10-B149",
                market_result="no",
                yes_count_fp="8.00",
                revenue=0,
                fee_cost="0.5000",
            ),
        ],
        fill_log_text=_log_line() + old_log,
        paper_rows=[],
        open_tickers={"KXTRUMPSAY-26OCT26-GOLF"},
    )
    # ZACH yes win 2.7509, MELA yes loss -5.2491, B149 yes loss -3.28.
    # GOLF is open. The sell, the blank action, and the scalar stay out.
    assert "running_total_dollars: -5.7782" in report
    assert "| KXTRUMPSAY-26OCT26-GOLF | yes | 8 | 89 | 0.0549 | open | open |" in report
    assert "sell fill; not allocated" in report
    assert "fill action absent" in report
    assert "scalar settlement; payout not allocated" in report
    assert "KXTRUTHSOCIAL-26OCT10-B149: fee column absent" in report
    assert "settlement fee 0.5 differs from fill fees 0.08" in report
    assert tickers == ["KXTRUMPSAY-26OCT19-ZACH"]
    assert "fill-order-1" not in report
    assert "order_id" not in report


def test_duplicate_economics_are_not_backfilled():
    report, new_log, tickers = assemble_pnl(
        fill_rows=[_buy(), _buy(order_id="fill-order-2")],
        settlement_rows=[_settlement(yes_count_fp="16.00", revenue=1600, fee_cost="0.2582")],
        fill_log_text=_log_line() + _log_line(),
        paper_rows=[],
        open_tickers=set(),
    )
    assert tickers == []
    assert new_log == _log_line() + _log_line()
    assert "running_total_dollars: 5.5018" in report


def test_paid_no_sell_is_a_winning_position_and_old_executed_rows_reconcile():
    """KXTRUTHSOCIAL-26OCT10-B149 is NO 8 at 84c. The fill action is sell.

    The settlement no_cost is 6.72, which is 8 * 0.84, and the NO won.
    Fee-net is 8 - 6.72 - 0.0753 = 1.2047. An executed log row with no fee
    column is a reconcile miss, not a dropped trade.
    """
    report, _new_log, _tickers = assemble_pnl(
        fill_rows=[
            _buy(
                ticker="KXTRUTHSOCIAL-26OCT10-B149",
                side="no",
                action="sell",
                yes_price_dollars="0.1600",
                no_price_dollars="0.8400",
                fee_cost="0.075300",
                order_id=None,
            )
        ],
        settlement_rows=[
            _settlement(
                ticker="KXTRUTHSOCIAL-26OCT10-B149",
                market_result="no",
                yes_count_fp="0.00",
                no_count_fp="8.00",
                yes_total_cost_dollars="0.0000",
                no_total_cost_dollars="6.7200",
                revenue=800,
                fee_cost="0.0753",
            )
        ],
        fill_log_text="2026-10-04T16:02:48Z, KXTRUTHSOCIAL-26OCT10-B149, no, 84, 8, old-client, 201\n"
        "2026-10-02T20:05:00Z, KXTRUTHSOCIAL-26OCT03-B129, yes, 36, 20, old-client, executed\n",
        paper_rows=[],
        open_tickers=set(),
    )
    assert "running_total_dollars: 1.2047" in report
    assert "KXTRUTHSOCIAL-26OCT10-B149: fee column absent" in report
    assert "sell fill; not allocated" not in report
    assert "KXTRUTHSOCIAL-26OCT03-B129: fill log row has no live fill" in report


def test_partial_fills_with_one_shared_id_reconcile_to_the_log_qty():
    """1 and 7 at 89c are one 8-contract log row when they share an id.

    Fees 0.0069 + 0.048 = 0.0549 dollars, which is 5.49 cents on the log.
    The report names the ticker and does not copy the id.
    """
    log = (
        "2026-10-19T16:21:34Z, KXTRUMPSAY-26OCT26-GOLF, yes, 89, 8, "
        f"{UUID_TEXT}, 201, 8, 89, 5.49, \n"
    )
    report, new_log, tickers = assemble_pnl(
        fill_rows=[
            _buy(
                ticker="KXTRUMPSAY-26OCT26-GOLF",
                count_fp="1.00",
                yes_price_dollars="0.8900",
                fee_cost="0.006900",
                order_id="fill-order-9",
            ),
            _buy(
                ticker="KXTRUMPSAY-26OCT26-GOLF",
                count_fp="7.00",
                yes_price_dollars="0.8900",
                fee_cost="0.048000",
                order_id="fill-order-9",
            ),
        ],
        settlement_rows=[],
        fill_log_text=log,
        paper_rows=[],
        open_tickers={"KXTRUMPSAY-26OCT26-GOLF"},
    )
    assert "running_total_dollars: 0" in report
    assert "| open | open |" in report
    assert "fill not in fill log" not in report
    assert "fill log fee differs" not in report
    assert tickers == ["KXTRUMPSAY-26OCT26-GOLF"]
    assert "fill-order-9" in new_log
    assert "fill-order-9" not in report
    assert UUID_TEXT not in report
    assert "order_id" not in report


def test_unsettled_missing_fill_is_a_mismatch_and_not_in_the_total():
    report, _new_log, _tickers = assemble_pnl(
        fill_rows=[_buy(ticker="KXTRUMPACT-26AUG30-T8")],
        settlement_rows=[],
        fill_log_text="",
        paper_rows=[],
        open_tickers=set(),
    )
    assert "fill has no settlement and is not open" in report
    assert "running_total_dollars: 0" in report
    assert "KXTRUMPACT-26AUG30-T8 |" not in report


def test_incomplete_report_omits_the_running_total():
    text = incomplete_report("exchange read failed", now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert "incomplete: exchange read failed" in text
    assert "running total omitted" in text
    assert "running_total_dollars" not in text


def test_collect_pages_fails_closed_when_the_cursor_does_not_end():
    def fetch(_cursor):
        return [{"ticker": "KX"}], "again"

    try:
        collect_pages(fetch, what="fills", max_pages=2)
    except ValueError as exc:
        assert "exceeded 2 pages" in str(exc)
    else:
        raise AssertionError("pagination must fail closed")


def test_run_report_writes_incomplete_without_a_total(tmp_path):
    class Broken:
        def list_fills_page(self, **_kwargs):
            raise ValueError("Kalshi fills response has invalid pagination")

    code = run_report(
        Broken(),
        repo=tmp_path,
        write_fill_log=False,
        now=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    text = (tmp_path / "logs" / "reports" / "pnl-20261020.md").read_text(encoding="utf-8")
    assert code == 1
    assert "running_total_dollars" not in text
    assert "running total omitted" in text


def test_replace_if_unchanged_skips_a_raced_write(tmp_path):
    path = tmp_path / "live-fill-log.md"
    path.write_text("original\n", encoding="utf-8")
    path.write_text("appended\n", encoding="utf-8")
    assert replace_if_unchanged(path, "original\n", "original\nfilled\n") is False
    assert path.read_text(encoding="utf-8") == "appended\n"


def test_backfill_write_uses_the_state_log_only(tmp_path):
    log_path = tmp_path / "logs" / "state" / "live-fill-log.md"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(_log_line(), encoding="utf-8")

    class Client:
        def list_fills_page(self, **_kwargs):
            return [_buy()], None

        def list_settlements_page(self, **_kwargs):
            return [_settlement()], None

        def get_open_positions(self, *, strict):
            assert strict is True
            return []

    code = run_report(
        Client(),
        repo=tmp_path,
        write_fill_log=True,
        now=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    report = (tmp_path / "logs" / "reports" / "pnl-20261020.md").read_text(encoding="utf-8")
    assert code == 0
    assert "fill-order-1" in log_path.read_text(encoding="utf-8")
    assert "fill-order-1" not in report
    assert "order_id" not in report
    assert "running_total_dollars: 2.7509" in report
