"""Hand-checked latch-fit cases. 8@84c and 8@64c trip the $5 line. 8@38c does not."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from scripts.latch_fit_report import (
    DESK_BANKROLL,
    LOSS_LIMIT_FRACTION,
    build_report,
    fit_row,
    main,
    session_loss_line,
)
from utils.event_news_session_halt import CASH_DROP_CENTS

_LINE = Decimal("5")


def _fit(cents: int, size: int = 8, **kwargs):
    return fit_row(
        origin="case",
        ticker="KXTEST-26NOV15-A",
        side="yes",
        series="KXTEST",
        price_cents=Decimal(cents),
        size=size,
        **kwargs,
    )


def test_line_is_ten_percent_of_a_fifty_dollar_bankroll() -> None:
    assert LOSS_LIMIT_FRACTION * DESK_BANKROLL == _LINE
    assert session_loss_line() == _LINE
    assert session_loss_line() == Decimal(CASH_DROP_CENTS) / Decimal(100)


def test_eight_at_84_and_64_trip_and_38_does_not() -> None:
    """Premium plus the Kalshi taker fee, checked by hand against quote_fee.

    8 * 0.84 + 0.0753 = 6.7953
    8 * 0.64 + 0.1291 = 5.2491
    8 * 0.38 + 0.1320 = 3.1720
    90% of $5 is $4.50, so the largest fitting sizes are 5, 6, and 11.
    """
    at_84 = _fit(84)
    at_64 = _fit(64)
    at_38 = _fit(38)

    assert at_84.fee == Decimal("0.0753")
    assert at_84.fee_source == "formula"
    assert at_84.worst_case_loss == Decimal("6.7953")
    assert at_84.would_8_trip is True
    assert at_84.max_size_90 == 5

    assert at_64.fee == Decimal("0.1291")
    assert at_64.worst_case_loss == Decimal("5.2491")
    assert at_64.would_8_trip is True
    assert at_64.max_size_90 == 6

    assert at_38.fee == Decimal("0.1320")
    assert at_38.worst_case_loss == Decimal("3.1720")
    assert at_38.would_8_trip is False
    assert at_38.max_size_90 == 11


def test_logged_fee_is_marked_and_used_for_an_eight_lot(tmp_path: Path) -> None:
    """12.91 cents is the logged 8@64c fee. A blank fee column uses the formula."""
    order_id = "order-should-not-appear"
    text = "\n".join(
        [
            "# Live fill log",
            (
                "2026-10-15T23:19:14Z, KXTRUMPSAY-26OCT19-MELA, yes, 64, 8, "
                f"client, 201, 8, 64, 12.91, {order_id}"
            ),
            "2026-10-27T07:54:56Z, KXTRUMPACT-26OCT25-T3, no, 38, 8, client, 201",
            "",
        ]
    )
    rows, lines = build_report(fill_text=text)
    assert [row.fee_source for row in rows] == ["logged", "formula"]
    assert rows[0].fee == Decimal("0.1291")
    assert rows[0].worst_case_loss == Decimal("5.2491")
    assert rows[0].would_8_trip is True
    assert rows[1].fee == Decimal("0.1320")
    assert rows[1].would_8_trip is False
    assert rows[0].series == "KXTRUMPSAY"
    assert rows[1].series == "KXTRUMPACT"
    rendered = "\n".join(lines)
    assert "eight_trips=1" in rendered
    assert "median_max_size_90=8.5" in rendered
    csv_path = tmp_path / "latch-fit.csv"
    assert main(["--fill-log", str(_write(tmp_path / "fills.md", text)), "--csv", str(csv_path)]) == 0
    written = csv_path.read_text(encoding="utf-8")
    assert order_id not in written
    assert "logged" in written
    assert "formula" in written


def test_attempt_jsonl_uses_the_formula_and_defaults_size_to_eight(tmp_path: Path) -> None:
    attempts = "\n".join(
        [
            '{"ticker":"KXTRUMPSAY-26NOV-A","side":"yes","price_cents":84,"series":"KXTRUMPSAY"}',
            '{"ticker":"KXTRUTHSOCIAL-26NOV-B","side":"no","price_cents":38,"series":"KXTRUTHSOCIAL"}',
        ]
    )
    rows, lines = build_report(attempt_text=attempts)
    assert [row.origin for row in rows] == ["attempt", "attempt"]
    assert [row.size for row in rows] == [8, 8]
    assert [row.fee_source for row in rows] == ["formula", "formula"]
    assert [row.would_8_trip for row in rows] == [True, False]
    assert "eight_trips=1" in "\n".join(lines)
    csv_path = tmp_path / "attempts.csv"
    source = _write(tmp_path / "attempts.jsonl", attempts + "\n")
    assert main(["--attempts", str(source), "--csv", str(csv_path)]) == 0
    assert "KXTRUMPSAY" in csv_path.read_text(encoding="utf-8")


def test_report_module_does_not_call_the_exchange_or_the_latch() -> None:
    text = Path("scripts/latch_fit_report.py").read_text(encoding="utf-8")
    assert "quote_fee" in text
    assert "place_limit_order" not in text
    assert "KalshiRestClient" not in text
    assert "politics_loss_limit_halt" not in text
    assert "get_balance" not in text


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path
