"""Fee-net replay for the politics desk flags.

The replay-ci gate still discovers only ``logs/edge_replay/corpus_*.jsonl``.
Commit 3a1c6c4 wrote ``tests/fixtures/edge_replay/corpus_politics_desk_history.jsonl``
with ``evidence_class=desk_history``. That file is outside discovery and is
not ``registered_oos``. This module reads it and prints the mismatch. It does
not change gate thresholds, and it does not call Kalshi or xAI.

History economics are the sealed paper rows and the exchange settlement rows
already pinned in ``politics_grok_fee_net_harness``. Open rows stay out of
fee-net. Risk-cap scaling is linear in contracts; Kalshi rounding means the
fee is not perfectly linear. The maker delta is a same-price quote, not a
fill probability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    KALSHI_GENERAL_2026_07_07,
    FeeContext,
    FeeRole,
    quote_fee,
)
from utils.event_news_desk_flags import (
    EVENT_NEWS_MAX_RISK,
    event_news_risk_capped_contracts,
    event_news_series_family,
    event_news_yes_floor_skip_reason,
)

D = Decimal
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests/fixtures/edge_replay/corpus_politics_desk_history.jsonl"
DISCOVERY_GLOB = "logs/edge_replay/corpus_*.jsonl"
TODAY_OPEN_COST_CENTS = 1872
TODAY_EQUITY_CENTS = 4640
TODAY_EXPOSURE_CAP_CENTS = 1160


@dataclass(frozen=True)
class HistoryRow:
    ticker: str
    side: str
    price_cents: int
    contracts: int
    fee_net: Decimal | None
    exchange_fee: Decimal | None
    source: str
    entered_at: datetime
    resolved_at: datetime | None
    resolution_known: bool


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# Paper economics and timestamps read 2026-10-13 from the sealed cohort.
# cost in that database includes the fee. fee_net is payout minus that cost.
PAPER_ROWS = (
    HistoryRow(
        "KXTRUTHSOCIAL-26AUG29-T240", "yes", 85, 8, D("1.1286"), D("0.0714"),
        "paper", _ts("2026-08-29T14:47:23.165994+00:00"),
        _ts("2026-08-30T16:49:23.111794+00:00"), True,
    ),
    HistoryRow(
        "KXTRUTHSOCIAL-26SEP05-T240", "no", 74, 8, D("1.9722"), D("0.1078"),
        "paper", _ts("2026-08-30T16:00:25.304003+00:00"),
        _ts("2026-09-06T16:13:16.549982+00:00"), True,
    ),
    HistoryRow(
        "KXTRUMPACT-26AUG30-T8", "no", 82, 8, D("1.3573"), D("0.0827"),
        "paper", _ts("2026-08-30T17:25:24.329773+00:00"),
        _ts("2026-09-06T16:43:18.799662+00:00"), True,
    ),
    HistoryRow(
        "KXTRUTHSOCIAL-26SEP12-B230", "no", 85, 6, D("0.8464"), D("0.0536"),
        "paper", _ts("2026-09-06T16:03:30.949185+00:00"),
        _ts("2026-09-13T14:52:33.084080+00:00"), True,
    ),
    HistoryRow(
        "KXTRUMPACT-26SEP06-T4", "no", 33, 22, D("-7.6005"), D("0.3405"),
        "paper", _ts("2026-09-06T19:23:40.188428+00:00"),
        _ts("2026-09-13T14:52:33.389138+00:00"), True,
    ),
    HistoryRow(
        "KXTRUMPSAY-26SEP14-UKRA", "yes", 60, 10, D("3.832"), D("0.168"),
        "paper", _ts("2026-09-11T19:18:57.674842+00:00"),
        _ts("2026-09-14T14:52:59.226439+00:00"), True,
    ),
)

# Live settlement economics are the harness rows. Entry stamps are the
# fill-log times. Settlement clocks for these three are not in the fixture,
# so family overlap against them is not measured. T6 settlement price is
# 42c (cost 3.36 on 8 contracts), which is the exchange figure.
LIVE_SETTLED_ROWS = (
    HistoryRow(
        "KXTRUTHSOCIAL-26OCT03-B129", "yes", 36, 20, D("-7.5226"), D("0.3226"),
        "live", _ts("2026-10-02T20:05:00+00:00"), None, False,
    ),
    HistoryRow(
        "KXTRUTHSOCIAL-26OCT10-B149", "no", 84, 8, D("1.2047"), D("0.0753"),
        "live", _ts("2026-10-04T16:02:48+00:00"), None, False,
    ),
    HistoryRow(
        "KXTRUMPACT-26OCT04-T6", "no", 42, 8, D("4.5035"), D("0.1365"),
        "live", _ts("2026-10-08T10:05:00+00:00"), None, False,
    ),
)

# Still open on 2026-10-13. Cost only. They must not be cancelled or resized.
OPEN_ROWS = (
    HistoryRow(
        "KXTRUTHSOCIAL-26OCT17-B129", "no", 85, 8, None, None,
        "live", _ts("2026-10-11T16:04:20+00:00"), None, True,
    ),
    HistoryRow(
        "KXTRUMPACT-26OCT11-T5", "no", 60, 8, None, None,
        "live", _ts("2026-10-11T17:16:27+00:00"), None, True,
    ),
    HistoryRow(
        "KXTRUMPSAY-26OCT19-GOLF", "yes", 89, 8, None, D("0.0549"),
        "live", _ts("2026-10-12T19:11:24+00:00"), None, True,
    ),
)

REALIZED_ROWS = PAPER_ROWS + LIVE_SETTLED_ROWS


def _money(value: Decimal) -> str:
    """Four decimal dollars. The risk-cap ratio is not a terminating cent."""
    rounded = value.quantize(D("0.0001"), rounding=ROUND_HALF_EVEN)
    return format(rounded, "f")


def baseline_fee_net(rows: tuple[HistoryRow, ...] = REALIZED_ROWS) -> Decimal:
    return sum((row.fee_net or D("0") for row in rows), D("0"))


def capped_contracts(row: HistoryRow) -> int:
    return event_news_risk_capped_contracts(
        row.contracts,
        row.price_cents,
        live=False,
        environ={},
    )


def risk_cap_at(
    cap_cents: int,
    rows: tuple[HistoryRow, ...] = REALIZED_ROWS,
) -> dict[str, str]:
    """Fee-net if the paper risk cap had been this many cents. Live is unchanged."""
    base = baseline_fee_net(rows)
    environ = {EVENT_NEWS_MAX_RISK: str(cap_cents)}
    total = D("0")
    positive = D("0")
    for row in rows:
        sized = event_news_risk_capped_contracts(
            row.contracts,
            row.price_cents,
            live=False,
            environ=environ,
        )
        scaled = _scale(row, sized)
        total += scaled
        positive += _positive_removed(row.fee_net or D("0"), scaled)
    return {
        "cap_cents": str(cap_cents),
        "n": str(len(rows)),
        "fee_net": _money(total),
        "delta": _money(total - base),
        "positive_fee_net_removed": _money(positive),
    }


def risk_cap_sweep(
    caps: tuple[int, ...] = (400, 500, 600, 800),
) -> list[dict[str, str]]:
    return [risk_cap_at(cap) for cap in caps]


def yes_floor_drops(row: HistoryRow) -> bool:
    return event_news_yes_floor_skip_reason(
        row.side,
        row.price_cents,
        live=False,
        environ={},
    ) == "yes_below_floor"


def _scale(row: HistoryRow, contracts: int) -> Decimal:
    fee_net = row.fee_net or D("0")
    if row.contracts <= 0:
        return D("0")
    return fee_net * D(contracts) / D(row.contracts)


def _quoted_fee(price_cents: int, contracts: int, role: FeeRole) -> Decimal:
    price = D(price_cents) / D("100")
    quantity = D(contracts)
    coefficient = D("0.0175") if role is FeeRole.MAKER else D("0.07")
    quote = quote_fee(
        FeeContext(
            schedule_id=KALSHI_GENERAL_2026_07_07,
            role=role,
            quantity=quantity,
            price=price,
            signed_revenue=quantity * price,
            order_id="politics-desk-replay",
            accumulator=D("0"),
            multiplier=D("1"),
            coefficient=coefficient,
            account_precision=DIRECT_ACCOUNT_PRECISION,
            timestamp=datetime(2026, 10, 12, tzinfo=timezone.utc),
        )
    )
    return quote.net_fee


def _maker_fee_savings(row: HistoryRow, contracts: int) -> Decimal:
    taker = _quoted_fee(row.price_cents, contracts, FeeRole.TAKER)
    maker = _quoted_fee(row.price_cents, contracts, FeeRole.MAKER)
    return taker - maker


def _family_blocks(row: HistoryRow, open_until: list[tuple[str, datetime | None]]) -> bool:
    family = event_news_series_family(row.ticker)
    for held_family, resolved_at in open_until:
        if held_family != family:
            continue
        if resolved_at is None or resolved_at > row.entered_at:
            return True
    return False


def _positive_removed(before: Decimal, after: Decimal) -> Decimal:
    if before <= 0:
        return D("0")
    return max(D("0"), before - after)


def replay_modes() -> list[dict[str, str]]:
    """One row per flag, plus baseline and all-four together."""
    base = baseline_fee_net()
    base_n = len(REALIZED_ROWS)

    yes_kept = [row for row in REALIZED_ROWS if not yes_floor_drops(row)]
    yes_total = baseline_fee_net(tuple(yes_kept))
    yes_positive = sum(
        (_positive_removed(row.fee_net or D("0"), D("0")) for row in REALIZED_ROWS if yes_floor_drops(row)),
        D("0"),
    )

    risk_total = D("0")
    risk_positive = D("0")
    for row in REALIZED_ROWS:
        sized = capped_contracts(row)
        scaled = _scale(row, sized)
        risk_total += scaled
        risk_positive += _positive_removed(row.fee_net or D("0"), scaled)

    family_kept: list[HistoryRow] = []
    family_ambiguous = False
    open_until: list[tuple[str, datetime | None]] = []
    for row in sorted(REALIZED_ROWS, key=lambda item: item.entered_at):
        if not row.resolution_known:
            family_ambiguous = True
        if _family_blocks(row, open_until):
            continue
        family_kept.append(row)
        if row.resolution_known:
            open_until.append((event_news_series_family(row.ticker), row.resolved_at))
    family_total = baseline_fee_net(tuple(family_kept))
    family_dropped = [row for row in REALIZED_ROWS if row not in family_kept]
    family_positive = sum(
        (_positive_removed(row.fee_net or D("0"), D("0")) for row in family_dropped),
        D("0"),
    )

    maker_savings = sum(
        (_maker_fee_savings(row, row.contracts) for row in REALIZED_ROWS),
        D("0"),
    )

    combined_total = D("0")
    combined_rows = 0
    combined_open: list[tuple[str, datetime | None]] = []
    ordered = sorted(REALIZED_ROWS + OPEN_ROWS, key=lambda item: item.entered_at)
    for row in ordered:
        if yes_floor_drops(row):
            continue
        if _family_blocks(row, combined_open):
            continue
        sized = capped_contracts(row)
        if row.fee_net is not None and sized > 0:
            combined_total += _scale(row, sized)
            combined_total += _maker_fee_savings(row, sized)
            combined_rows += 1
        if row.resolution_known:
            combined_open.append((event_news_series_family(row.ticker), row.resolved_at))

    return [
        {
            "mode": "baseline",
            "n": str(base_n),
            "fee_net": _money(base),
            "delta": _money(D("0")),
            "positive_fee_net_removed": _money(D("0")),
            "note": "6 sealed paper rows plus 3 settled live rows; 3 open rows are cost only",
        },
        {
            "mode": "yes_floor",
            "n": str(len(yes_kept)),
            "fee_net": _money(yes_total),
            "delta": _money(yes_total - base),
            "positive_fee_net_removed": _money(yes_positive),
            "note": "YES ask below 55c is dropped; NO at 33c and 36c stays",
        },
        {
            "mode": "risk_cap",
            "n": str(base_n),
            "fee_net": _money(risk_total),
            "delta": _money(risk_total - base),
            "positive_fee_net_removed": _money(risk_positive),
            "note": "paper default cap is 600 cents; fees are not perfectly linear after scaling",
        },
        {
            "mode": "one_per_family",
            "n": str(len(family_kept)),
            "fee_net": _money(family_total),
            "delta": _money(family_total - base),
            "positive_fee_net_removed": _money(family_positive),
            "note": (
                "paper overlap is measured; live settlement clocks are absent so "
                "live family overlap is not_measured"
                if family_ambiguous
                else "paper and live overlap measured from entry and resolution timestamps"
            ),
        },
        {
            "mode": "exposure",
            "n": str(base_n),
            "fee_net": "not_measured",
            "delta": "not_measured",
            "positive_fee_net_removed": "not_measured",
            "note": (
                f"equity at each historical entry is unknown; "
                f"2026-10-13 open cost {TODAY_OPEN_COST_CENTS} cents "
                f"> {TODAY_EXPOSURE_CAP_CENTS} cents "
                f"(25 percent of equity {TODAY_EQUITY_CENTS})"
            ),
        },
        {
            "mode": "maker",
            "n": str(base_n),
            "fee_net": _money(base + maker_savings),
            "delta": _money(maker_savings),
            "positive_fee_net_removed": _money(D("0")),
            "note": "same-price maker quote 0.0175 versus taker 0.07; fill probability is not modeled",
        },
        {
            "mode": "all",
            "n": str(combined_rows),
            "fee_net": _money(combined_total),
            "delta": _money(combined_total - base),
            "positive_fee_net_removed": "not_separated",
            "note": "yes floor, then family, then risk-cap scale, then maker quote; exposure stays not_measured",
        },
    ]


def corpus_mismatch() -> dict[str, object]:
    """Read the 3a1c6c4 fixture and say why the gate cannot use it."""
    text = FIXTURE.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    classes = sorted({str(row.get("evidence_class")) for row in rows})
    return {
        "fixture": "tests/fixtures/edge_replay/corpus_politics_desk_history.jsonl",
        "discovery_glob": DISCOVERY_GLOB,
        "discovery_recursive": False,
        "fixture_inside_discovery": False,
        "row_count": len(rows),
        "evidence_class": classes,
        "required_evidence_class": "registered_oos",
        "format_mismatch": classes != ["registered_oos"],
        "path_mismatch": True,
        "gate_rule_change": False,
        "failure_reason": "insufficient corpus",
    }


def format_politics_replay() -> str:
    mismatch = corpus_mismatch()
    lines = [
        "=== Politics desk replay ===",
        (
            "corpus_mismatch "
            f"fixture={mismatch['fixture']} "
            f"discovery={mismatch['discovery_glob']} "
            f"evidence_class={','.join(mismatch['evidence_class'])} "
            f"required={mismatch['required_evidence_class']} "
            f"rows={mismatch['row_count']} "
            f"failure_reason={mismatch['failure_reason']}"
        ),
        "mode n fee_net delta positive_fee_net_removed note",
    ]
    for row in replay_modes():
        lines.append(
            f"{row['mode']} n={row['n']} fee_net={row['fee_net']} "
            f"delta={row['delta']} "
            f"positive_fee_net_removed={row['positive_fee_net_removed']} "
            f"note={row['note']}"
        )
    return "\n".join(lines)


def main() -> None:
    print(format_politics_replay())


if __name__ == "__main__":
    main()
