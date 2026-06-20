"""Reusable operator-throughput leading indicators.

Reporting-only helpers. Trade-log callers must stream rows into these helpers
instead of materializing raw log text.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from utils.diagnostics_script_helpers import is_test_record_source_only
from utils.trade_log_reader import iter_trade_records


@dataclass(frozen=True)
class ThroughputOperatorSummary:
    opportunities: int
    skipped: int
    paper_trades: int
    window_days: float
    opportunities_per_day: float
    skipped_per_opportunity: float | None
    top_ticker_trades_per_day: list[tuple[str, float]]
    opportunity_age_p50_seconds: float | None = None
    opportunity_age_p90_seconds: float | None = None

    @property
    def opportunity_age_available(self) -> bool:
        return (
            self.opportunity_age_p50_seconds is not None
            and self.opportunity_age_p90_seconds is not None
        )


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("type") or event.get("event") or "").strip()


def _ticker(event: dict[str, Any]) -> str | None:
    for key in ("ticker", "market_ticker", "series_ticker"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _window_days(window_start: datetime, window_end: datetime) -> float:
    seconds = (window_end - window_start).total_seconds()
    if seconds <= 0:
        return 0.0
    return seconds / 86_400.0


def _in_window(event: dict[str, Any], window_start: datetime, window_end: datetime) -> bool:
    ts = _parse_ts(event.get("ts") or event.get("timestamp"))
    if ts is None:
        return True
    return window_start <= ts <= window_end


def _numeric_age_seconds(event: dict[str, Any]) -> float | None:
    for key in (
        "opportunity_age_seconds",
        "age_seconds",
        "source_age_seconds",
        "candidate_age_seconds",
    ):
        value = event.get(key)
        try:
            age = float(value)
        except (TypeError, ValueError):
            continue
        if age >= 0:
            return age
    for key in ("opportunity_age_ms", "age_ms"):
        value = event.get(key)
        try:
            age_ms = float(value)
        except (TypeError, ValueError):
            continue
        if age_ms >= 0:
            return age_ms / 1000.0
    return None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * quantile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_operator_throughput(
    events: Iterable[dict[str, Any]],
    *,
    window_start: datetime,
    window_end: datetime,
) -> ThroughputOperatorSummary:
    days = _window_days(window_start, window_end)
    opportunities = 0
    skipped = 0
    paper_trades = 0
    trade_tickers: Counter[str] = Counter()
    opportunity_ages: list[float] = []

    for event in events:
        if not _in_window(event, window_start, window_end):
            continue
        event_type = _event_type(event)
        if event_type == "OPPORTUNITY":
            opportunities += 1
            age = _numeric_age_seconds(event)
            if age is not None:
                opportunity_ages.append(age)
        elif event_type == "SKIPPED":
            skipped += 1
        elif event_type == "PAPER_TRADE":
            paper_trades += 1
            ticker = _ticker(event)
            if ticker:
                trade_tickers[ticker] += 1

    rate_denominator = days if days > 0 else 1.0
    return ThroughputOperatorSummary(
        opportunities=opportunities,
        skipped=skipped,
        paper_trades=paper_trades,
        window_days=days,
        opportunities_per_day=opportunities / rate_denominator,
        skipped_per_opportunity=skipped / opportunities if opportunities else None,
        top_ticker_trades_per_day=[
            (ticker, count / rate_denominator)
            for ticker, count in trade_tickers.most_common()
        ],
        opportunity_age_p50_seconds=_percentile(opportunity_ages, 0.5),
        opportunity_age_p90_seconds=_percentile(opportunity_ages, 0.9),
    )


def summarize_operator_throughput_from_trade_log(
    trades_path: Path,
    *,
    window_start: datetime,
    window_end: datetime,
    exclude_test: bool = True,
) -> ThroughputOperatorSummary:
    def iter_events() -> Iterable[dict[str, Any]]:
        for row in iter_trade_records(trades_path, since=window_start, until=window_end):
            if exclude_test and is_test_record_source_only(row):
                continue
            yield row

    return summarize_operator_throughput(
        iter_events(),
        window_start=window_start,
        window_end=window_end,
    )


def _fmt_age(seconds: float) -> str:
    return f"{seconds / 60.0:.1f}m"


def format_operator_throughput_lines(
    summary: ThroughputOperatorSummary,
    *,
    top: int = 5,
) -> list[str]:
    lines = ["OPERATOR THROUGHPUT LEADING INDICATORS"]
    lines.append(
        "  Opportunities/day              : "
        f"{summary.opportunities_per_day:.2f} "
        f"({summary.opportunities} opportunities / {summary.window_days:.1f}d)"
    )
    if summary.skipped_per_opportunity is None:
        ratio = "unavailable"
    else:
        ratio = f"{summary.skipped_per_opportunity:.3f}"
    lines.append(
        "  Skipped/opportunity ratio      : "
        f"{ratio} ({summary.skipped} skipped / {summary.opportunities} opportunities)"
    )
    if summary.opportunity_age_available:
        lines.append(
            "  Opportunity age p50/p90        : "
            f"{_fmt_age(summary.opportunity_age_p50_seconds or 0.0)} / "
            f"{_fmt_age(summary.opportunity_age_p90_seconds or 0.0)}"
        )
    else:
        lines.append("  Opportunity age p50/p90        : unavailable")
    if summary.top_ticker_trades_per_day:
        lines.append("  Top tickers by trades/day      :")
        for ticker, trades_per_day in summary.top_ticker_trades_per_day[:top]:
            lines.append(f"    {ticker}: {trades_per_day:.2f}/day")
    else:
        lines.append("  Top tickers by trades/day      : unavailable")
    return lines
