"""The harness must match the reconciled Kalshi rows to the cent."""

from decimal import Decimal

from scripts.simulations.politics_grok_fee_net_harness import (
    GOLF_FILL,
    SETTLED_ROWS,
    bootstrap_mean_ci,
    cash_after_golf_cents,
    quoted_taker_fee,
    summarize,
    variant_table,
)


def test_quoted_fees_match_exchange_fee_cost():
    for row in SETTLED_ROWS:
        quoted = quoted_taker_fee(row.price, row.quantity, row.cost)
        assert quoted == row.exchange_fee
    golf = quoted_taker_fee(
        GOLF_FILL["price"],
        GOLF_FILL["quantity"],
        GOLF_FILL["cost"],
    )
    assert golf == GOLF_FILL["exchange_fee"]


def test_settled_fee_net_matches_the_cent():
    assert [row.fee_net for row in SETTLED_ROWS] == [
        Decimal("-7.5226"),
        Decimal("1.2047"),
        Decimal("4.5035"),
    ]
    stats = summarize()
    assert stats["total"] == Decimal("-1.8144")
    assert stats["max_drawdown"] == Decimal("7.5226")
    assert stats["n"] == Decimal("3")


def test_golf_cash_drop_has_no_unexplained_cent():
    balance, unexplained = cash_after_golf_cents()
    assert balance == 2920
    assert unexplained == 0


def test_bootstrap_ci_is_reproducible_and_veto_is_not_measured():
    values = [row.fee_net for row in SETTLED_ROWS]
    first = bootstrap_mean_ci(values)
    second = bootstrap_mean_ci(values)
    assert first == second
    # Each of 3 rows has probability 1/27, above 2.5%, so this interval
    # is the sample range. It is not a prospective veto CI.
    assert first == (min(values), max(values))
    assert first == (Decimal("-7.5226"), Decimal("4.5035"))
    variants = {row["variant"]: row for row in variant_table()}
    assert variants["baseline"]["total"] == "-1.8144"
    assert variants["current_veto"]["leak_safe"] == "not_run"
    assert variants["current_veto"]["total"] == "not_measured"
    assert variants["current_veto"]["mean"] == "not_measured"
    assert variants["grounded_veto"]["leak_safe"] == "no"
    assert variants["grounded_veto"]["total"] == "not_measured"
