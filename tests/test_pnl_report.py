"""Fee-net P&L stays on the exchange fills and never prints an order id."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from decimal import Decimal

from scripts.pnl_report import (
    assemble_pnl,
    collect_pages,
    incomplete_report,
    reconciliation_lines,
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
    assert "| yes | 44.84h | KXTRUMPSAY |" in report
    assert "| KXTRUMPSAY | 1 | not enough data (n=1) | not enough data (n=1) |" in report
    assert "reconciliation_gap: unreadable" in report
    assert "KXTRUTHSOCIAL-26AUG29-T240" in report
    assert "Not included in the live fee-net total." in report
    assert "order_id" not in report
    assert UUID_TEXT not in report
    assert "fill-order-1" not in report
    assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", report)
    assert tickers == ["KXTRUMPSAY-26OCT19-ZACH"]
    assert "fill-order-1" in new_log
    assert "backfilled_rows: KXTRUMPSAY-26OCT19-ZACH" in report


def test_polymarket_rows_do_not_change_the_live_total():
    """Stored paper settlement is shown by venue and stays out of the running total."""
    report, _new_log, _tickers = assemble_pnl(
        fill_rows=[_buy()],
        settlement_rows=[_settlement()],
        fill_log_text=_log_line(),
        paper_rows=[],
        open_tickers=set(),
        now=datetime(2026, 10, 20, tzinfo=timezone.utc),
        polymarket_rows=[
            {
                "venue": "polymarket_us",
                "series": "PMTRUMPAPPROVE",
                "ticker": "parc-trmpapprv-wk-2026-10-23-37pt7-37pt9",
                "queued": "0",
                "settlement": "not settled",
            }
        ],
    )
    assert "running_total_dollars: 2.7509" in report
    assert "| KXTRUMPSAY | 1 | not enough data (n=1) | not enough data (n=1) |" in report
    assert "## Polymarket paper rows" in report
    assert "| polymarket_us | PMTRUMPAPPROVE | parc-trmpapprv-wk-2026-10-23-37pt7-37pt9 | 0 | not settled |" in report
    assert report.index("## Polymarket paper rows") < report.index("## Backfill")
    assert "Not included in the live fee-net total." in report


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
    assert "| KXTRUMPSAY-26OCT26-GOLF | yes | 8 | 89 |" in report
    assert "| yes | 1 | 89 |" not in report
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


def test_open_sell_labeled_no_partials_stay_out_of_the_settled_total():
    """KXTRUMPACT-26OCT25-T3 is an open NO. The exchange labels the buy as sell.

    0.01 and 7.99 share one id, one price, and one 8-contract log row.
    The display row is count 8 and fee 0.132. Fee-net stays 0 until
    settlement. Open cost is 8 * 0.38 = 3.04. Cash 46.828 plus mark 2.80
    minus funding 50 is -0.372, and so is the open adjustment.
    """
    log = (
        "2026-10-27T07:54:56Z, KXTRUMPACT-26OCT25-T3, no, 38, 8, "
        f"{UUID_TEXT}, 201, 8, 38, 13.2, \n"
    )
    report, _new_log, _tickers = assemble_pnl(
        fill_rows=[
            _buy(
                ticker="KXTRUMPACT-26OCT25-T3",
                side="no",
                action="sell",
                count_fp="0.01",
                yes_price_dollars="0.6200",
                no_price_dollars="0.3800",
                fee_cost="0.000200",
                order_id="fill-order-t3",
                created_time="2026-10-27T07:54:54Z",
            ),
            _buy(
                ticker="KXTRUMPACT-26OCT25-T3",
                side="no",
                action="sell",
                count_fp="7.99",
                yes_price_dollars="0.6200",
                no_price_dollars="0.3800",
                fee_cost="0.131800",
                order_id="fill-order-t3",
                created_time="2026-10-27T07:54:54Z",
            ),
        ],
        settlement_rows=[],
        fill_log_text=log,
        paper_rows=[],
        open_tickers={"KXTRUMPACT-26OCT25-T3": Decimal("-8.00")},
        now=datetime(2026, 10, 27, 8, 54, 54, tzinfo=timezone.utc),
        cash=Decimal("46.828"),
        portfolio_value=Decimal("2.80"),
        starting_balance=Decimal("50"),
    )
    assert "running_total_dollars: 0" in report
    assert "sell fill; not allocated" not in report
    assert "fill not in fill log" not in report
    assert "fill log fee differs" not in report
    assert "| KXTRUMPACT-26OCT25-T3 | no | 8 | 38 | 0.132 | open | open |" in report
    assert "| no | 0.01 | 38 |" not in report
    assert "| no | 7.99 | 38 |" not in report
    assert "| open | 1h | KXTRUMPACT |" in report
    assert "open_cost: 3.04" in report
    assert "open_entry_fees: 0.132" in report
    assert "reconciliation_left: -0.372" in report
    assert "reconciliation_right: -0.372" in report
    assert "\nresidual: 0\n" in report
    assert "closes_within_25_cents: yes" in report
    assert "unsettled: open cost plus open fees is not the portfolio mark" not in report
    assert "not enough data (n=0)" in report
    assert UUID_TEXT not in report
    assert "fill-order-t3" not in report


def test_reconciliation_closes_at_25_cents_and_names_a_larger_gap():
    """25 cents is inside the band. 26 cents is not a closed book.

    Left is cash + mark - funding. Right is settled fee-net when the book
    is flat. Residual is left minus right.
    """
    closed = "\n".join(reconciliation_lines(
        ledger=Decimal("1"), open_exposure=Decimal("0"), open_fees=Decimal("0"),
        cash=Decimal("1"), portfolio=Decimal("0"), starting=Decimal("0.25"),
    ))
    # left 1 + 0 - 0.25 = 0.75. right 1. residual -0.25.
    assert "cash: 1" in closed
    assert "portfolio_mark: 0" in closed
    assert "applied_net_funding: 0.25" in closed
    assert "settled_fee_net: 1" in closed
    assert "open_mark: 0" in closed
    assert "open_cost: 0" in closed
    assert "open_entry_fees: 0" in closed
    assert "open_mark_minus_cost_and_fees: 0" in closed
    assert "reconciliation_left: 0.75" in closed
    assert "reconciliation_right: 1" in closed
    assert "\nresidual: -0.25\n" in closed
    assert "closes_within_25_cents: yes" in closed
    assert "reason:" not in closed
    open_gap = "\n".join(reconciliation_lines(
        ledger=Decimal("1"), open_exposure=Decimal("0"), open_fees=Decimal("0"),
        cash=Decimal("1"), portfolio=Decimal("0"), starting=Decimal("0.26"),
    ))
    assert "\nresidual: -0.26\n" in open_gap
    assert "closes_within_25_cents: no" in open_gap
    assert "deposits, fee rounding, or unsettled mark versus cost" in open_gap


def test_reconciliation_identity_is_zero_on_the_last_account_read():
    """The last live read closes. Open cost is not added on both sides.

    53.8724 + 3.20 - 50 = 7.0724. Settled fee-net 7.0444 plus
    (3.20 - 3.04 - 0.132) is the same 7.0724.
    """
    text = "\n".join(reconciliation_lines(
        ledger=Decimal("7.0444"),
        open_exposure=Decimal("3.04"),
        open_fees=Decimal("0.132"),
        cash=Decimal("53.8724"),
        portfolio=Decimal("3.20"),
        starting=Decimal("50"),
    ))
    assert "cash: 53.8724" in text
    assert "portfolio_mark: 3.2" in text
    assert "applied_net_funding: 50" in text
    assert "reconciliation_left: 7.0724" in text
    assert "settled_fee_net: 7.0444" in text
    assert "open_mark: 3.2" in text
    assert "open_cost: 3.04" in text
    assert "open_entry_fees: 0.132" in text
    assert "open_mark_minus_cost_and_fees: 0.028" in text
    assert "reconciliation_right: 7.0724" in text
    assert "\nresidual: 0\n" in text
    assert "closes_within_25_cents: yes" in text


def test_one_cent_log_price_uses_the_exchange_fill_and_a_split_stays_open():
    """A unique log row one cent off the fill is that fill.

    KXTRUMPACT-26OCT04-T6 is 8 NO at the exchange price of 42c. The log
    says 43c and has no fee column. The row shows 42c and the log text is
    not rewritten. One log row of 8 at 67c against 1 at 66c plus 7 at 67c
    stays a mismatch. A two-cent gap stays a mismatch too.
    """
    log = (
        "2026-10-08T00:00:00Z, KXTRUMPACT-26OCT04-T6, no, 43, 8, old-client, 201\n"
        "2026-10-23T00:00:00Z, KXTRUMPACT-26OCT18-T6, yes, 67, 8, old-client, 201, 8, 67, 12.41, \n"
        "2026-10-01T00:00:00Z, KXTRUMPACT-26OCT01-T1, yes, 40, 8, old-client, 201\n"
    )
    report, new_log, _tickers = assemble_pnl(
        fill_rows=[
            _buy(
                ticker="KXTRUMPACT-26OCT04-T6",
                side="no",
                count_fp="8.00",
                yes_price_dollars="0.5800",
                no_price_dollars="0.4200",
                fee_cost="0.136500",
                order_id="fill-order-t6",
            ),
            _buy(
                ticker="KXTRUMPACT-26OCT18-T6",
                count_fp="1.00",
                yes_price_dollars="0.6600",
                fee_cost="0.015800",
                order_id="fill-order-split",
            ),
            _buy(
                ticker="KXTRUMPACT-26OCT18-T6",
                count_fp="7.00",
                yes_price_dollars="0.6700",
                fee_cost="0.108300",
                order_id="fill-order-split",
            ),
            _buy(
                ticker="KXTRUMPACT-26OCT01-T1",
                count_fp="8.00",
                yes_price_dollars="0.4200",
                fee_cost="0.100000",
                order_id="fill-order-gap",
            ),
        ],
        settlement_rows=[
            _settlement(
                ticker="KXTRUMPACT-26OCT04-T6",
                market_result="no",
                yes_count_fp="0.00",
                no_count_fp="8.00",
                yes_total_cost_dollars="0.0000",
                no_total_cost_dollars="3.3600",
                revenue=800,
                fee_cost="0.1365",
            ),
            _settlement(
                ticker="KXTRUMPACT-26OCT18-T6",
                yes_count_fp="8.00",
                yes_total_cost_dollars="5.3500",
                revenue=800,
                fee_cost="0.1241",
            ),
            _settlement(
                ticker="KXTRUMPACT-26OCT01-T1",
                yes_count_fp="8.00",
                yes_total_cost_dollars="3.3600",
                revenue=800,
                fee_cost="0.1000",
            ),
        ],
        fill_log_text=log,
        paper_rows=[],
        open_tickers=set(),
    )
    assert "| KXTRUMPACT-26OCT04-T6 | no | 8 | 42 |" in report
    assert "| no | 8 | 43 |" not in report
    assert "KXTRUMPACT-26OCT04-T6: fill not in fill log" not in report
    assert "KXTRUMPACT-26OCT04-T6: fill log row has no live fill" not in report
    assert "KXTRUMPACT-26OCT04-T6: fee column absent" in report
    assert "| KXTRUMPACT-26OCT18-T6 | yes | 1 | 66 |" in report
    assert "| KXTRUMPACT-26OCT18-T6 | yes | 7 | 67 |" in report
    assert "KXTRUMPACT-26OCT18-T6: fill not in fill log" in report
    assert "KXTRUMPACT-26OCT18-T6: fill log row has no live fill" in report
    assert "| KXTRUMPACT-26OCT01-T1 | yes | 8 | 42 |" in report
    assert "KXTRUMPACT-26OCT01-T1: fill not in fill log" in report
    assert "KXTRUMPACT-26OCT01-T1: fill log row has no live fill" in report
    assert new_log == log
    assert "fill-order-t6" not in report
    assert "order_id" not in report


def test_same_price_on_two_orders_stays_two_rows():
    """Two orders at one price are two positions. A missing order id does not merge them."""
    report, _new_log, _tickers = assemble_pnl(
        fill_rows=[
            _buy(
                ticker="KXTRUMPSAY-26OCT26-GOLF",
                count_fp="8.00",
                yes_price_dollars="0.8900",
                fee_cost="0.050000",
                order_id="fill-order-a",
            ),
            _buy(
                ticker="KXTRUMPSAY-26OCT26-GOLF",
                count_fp="8.00",
                yes_price_dollars="0.8900",
                fee_cost="0.050000",
                order_id="fill-order-b",
            ),
            _buy(
                ticker="KXTRUMPSAY-26OCT19-MELA",
                count_fp="8.00",
                yes_price_dollars="0.6400",
                fee_cost="0.050000",
                order_id=None,
            ),
            _buy(
                ticker="KXTRUMPSAY-26OCT19-MELA",
                count_fp="8.00",
                yes_price_dollars="0.6400",
                fee_cost="0.050000",
                order_id=None,
            ),
        ],
        settlement_rows=[],
        fill_log_text="",
        paper_rows=[],
        open_tickers={
            "KXTRUMPSAY-26OCT26-GOLF": Decimal("16.00"),
            "KXTRUMPSAY-26OCT19-MELA": Decimal("16.00"),
        },
    )
    assert report.count("| KXTRUMPSAY-26OCT26-GOLF | yes | 8 | 89 |") == 2
    assert report.count("| KXTRUMPSAY-26OCT19-MELA | yes | 8 | 64 |") == 2
    assert "| yes | 16 | 89 |" not in report
    assert "open size differs from fills" not in report
    assert "fill-order-a" not in report
