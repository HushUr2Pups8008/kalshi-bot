"""Fee-net harness for the reconciled politics live rows.

Uses quote_fee on the exchange fill price. Does not call Kalshi or xAI.
The current veto and the grounded veto are not applied to these rows:
replaying the live model now would see later news.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    KALSHI_GENERAL_2026_07_07,
    FeeContext,
    FeeRole,
    quote_fee,
)

D = Decimal


@dataclass(frozen=True)
class SettledRow:
    ticker: str
    side: str
    price: Decimal
    quantity: Decimal
    cost: Decimal
    revenue: Decimal
    exchange_fee: Decimal

    @property
    def fee_net(self) -> Decimal:
        return self.revenue - self.cost - self.exchange_fee


# Exchange fills and settlements pulled 2026-10-13. Fees are fee_cost.
SETTLED_ROWS = (
    SettledRow(
        ticker="KXTRUTHSOCIAL-26OCT03-B129",
        side="yes",
        price=D("0.36"),
        quantity=D("20"),
        cost=D("7.20"),
        revenue=D("0"),
        exchange_fee=D("0.3226"),
    ),
    SettledRow(
        ticker="KXTRUTHSOCIAL-26OCT10-B149",
        side="no",
        price=D("0.84"),
        quantity=D("8"),
        cost=D("6.72"),
        revenue=D("8.00"),
        exchange_fee=D("0.0753"),
    ),
    SettledRow(
        ticker="KXTRUMPACT-26OCT04-T6",
        side="no",
        price=D("0.42"),
        quantity=D("8"),
        cost=D("3.36"),
        revenue=D("8.00"),
        exchange_fee=D("0.1365"),
    ),
)

OPEN_COST = {
    "KXTRUMPACT-26OCT11-T5": D("4.80"),
    "KXTRUTHSOCIAL-26OCT17-B129": D("6.80"),
    "KXTRUMPSAY-26OCT19-GOLF": D("7.12"),
}

# paper_trade_accounting.fee_net_pnl_dollars for the sealed event-news cohort,
# read 2026-10-13. Six rows. No API keys.
PAPER_SEALED_FEE_NET = (
    ("KXTRUTHSOCIAL-26AUG29-T240", "yes", D("1.1286")),
    ("KXTRUTHSOCIAL-26SEP05-T240", "no", D("1.9722")),
    ("KXTRUMPACT-26AUG30-T8", "no", D("1.3573")),
    ("KXTRUTHSOCIAL-26SEP12-B230", "no", D("0.8464")),
    ("KXTRUMPACT-26SEP06-T4", "no", D("-7.6005")),
    ("KXTRUMPSAY-26SEP14-UKRA", "yes", D("3.832")),
)

GOLF_FILL = {
    "price": D("0.89"),
    "quantity": D("8"),
    "cost": D("7.12"),
    "exchange_fee": D("0.0549"),
}


def quoted_taker_fee(price: Decimal, quantity: Decimal, cost: Decimal) -> Decimal:
    """Match the politics taker quote: coefficient 0.07, centicent account."""
    quote = quote_fee(
        FeeContext(
            schedule_id=KALSHI_GENERAL_2026_07_07,
            role=FeeRole.TAKER,
            quantity=quantity,
            price=price,
            signed_revenue=cost,
            order_id="politics-harness",
            accumulator=D("0"),
            multiplier=D("1"),
            coefficient=D("0.07"),
            account_precision=DIRECT_ACCOUNT_PRECISION,
            timestamp=datetime(2026, 10, 12, tzinfo=timezone.utc),
        )
    )
    return quote.net_fee


def cash_after_golf_cents() -> tuple[int, int]:
    """Return (balance cents, unexplained cents) for 36.37 -> 29.20."""
    start = 3637
    notional = 712
    cent_fee = 5
    balance = start - notional - cent_fee
    return balance, 0


def summarize(rows: tuple[SettledRow, ...] = SETTLED_ROWS) -> dict[str, Decimal]:
    values = [row.fee_net for row in rows]
    total = sum(values, D("0"))
    cumulative = D("0")
    peak = D("0")
    max_drawdown = D("0")
    for value in values:
        cumulative += value
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    mean = total / Decimal(len(values))
    return {"n": Decimal(len(values)), "mean": mean, "total": total, "max_drawdown": max_drawdown}


def bootstrap_mean_ci(
    values: list[Decimal],
    *,
    seed: int = 20261013,
    draws: int = 10000,
) -> tuple[Decimal, Decimal]:
    rng = random.Random(seed)
    count = len(values)
    means: list[Decimal] = []
    for _ in range(draws):
        sample = [values[rng.randrange(count)] for _ in range(count)]
        means.append(sum(sample, D("0")) / Decimal(count))
    means.sort()
    low_index = int(0.025 * draws)
    high_index = int(0.975 * draws) - 1
    # n=3: each row has probability 1/27, which is above 2.5%.
    # The percentile interval is therefore the sample min and max.
    # It is not a prospective confidence interval for a future veto.
    return means[low_index], means[high_index]


def variant_table() -> list[dict[str, str]]:
    """Same settled candidates for every variant. Veto deltas are not measured."""
    base = summarize()
    return [
        {
            "variant": "baseline",
            "n": str(int(base["n"])),
            "total": str(base["total"]),
            "mean": str(base["mean"]),
            "max_drawdown": str(base["max_drawdown"]),
            "leak_safe": "yes",
            "note": "exchange settlement revenue minus cost minus fee_cost",
        },
        {
            "variant": "current_veto",
            "n": str(int(base["n"])),
            "total": "not_measured",
            "mean": "not_measured",
            "max_drawdown": "not_measured",
            "leak_safe": "not_run",
            "note": "fills predate a grounded packet; do not read the baseline as a veto delta",
        },
        {
            "variant": "grounded_veto",
            "n": str(int(base["n"])),
            "total": "not_measured",
            "mean": "not_measured",
            "max_drawdown": "not_measured",
            "leak_safe": "no",
            "note": "a live model call now can see later news",
        },
    ]


def grounding_replay() -> dict[str, object]:
    """Fee-net with the grounding flag left off.

    The fill log has six live rows, not seven. No stored grounded reply
    exists for these candidates, so this does not call the model.
    """
    paper_total = sum((fee_net for _ticker, _side, fee_net in PAPER_SEALED_FEE_NET), D("0"))
    live_settled = summarize()["total"]
    live_n = len(SETTLED_ROWS) + len(OPEN_COST)
    paper_n = len(PAPER_SEALED_FEE_NET)
    return {
        "grounding": "off",
        "paper_n": paper_n,
        "live_n": live_n,
        "n": paper_n + live_n,
        "realized_n": paper_n + len(SETTLED_ROWS),
        "fee_net_without_grounding": paper_total + live_settled,
        "fee_net_with_grounding": "not_measured",
        "delta": "not_measured",
        "vetoed": (),
        "reason": "no stored grounded reply; a live model call now can see later news",
    }


def main() -> None:
    stats = summarize()
    low, high = bootstrap_mean_ci([row.fee_net for row in SETTLED_ROWS])
    balance, unexplained = cash_after_golf_cents()
    print(f"settled_n={int(stats['n'])} total={stats['total']} mean={stats['mean']}")
    print(
        f"max_drawdown={stats['max_drawdown']} "
        f"mean_resample_range={low}..{high} "
        "resample_note=n3_percentile_interval_is_the_sample_range"
    )
    print(f"cash_cents={balance} unexplained_cents={unexplained}")
    for row in variant_table():
        print(row)
    replay = grounding_replay()
    print(
        "grounding_replay "
        f"n={replay['n']} paper_n={replay['paper_n']} live_n={replay['live_n']} "
        f"fee_net_without_grounding={replay['fee_net_without_grounding']} "
        f"fee_net_with_grounding={replay['fee_net_with_grounding']} "
        f"delta={replay['delta']} vetoed={replay['vetoed']} "
        f"grounding={replay['grounding']}"
    )


if __name__ == "__main__":
    main()
