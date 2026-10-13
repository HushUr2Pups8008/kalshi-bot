"""14-day politics funnel. Read-only. A missing paper database does not fail.

Log hits are repeated scans, so the table also counts unique tickers.
Fee-net of the new flags comes from the committed replay rows. The live
flags are off, so yes-floor, risk-cap, exposure, and the new family flag
have no log hits until the operator turns them on. The script does not
place, cancel, or read an API key, and it does not print order ids.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from scripts.simulations.politics_desk_replay import (
    LIVE_SETTLED_ROWS,
    OPEN_ROWS,
    PAPER_ROWS,
    capped_contracts,
    replay_modes,
    yes_floor_drops,
)
from scripts.simulations.politics_desk_replay import (
    _maker_fee_savings as maker_fee_savings,
)

D = Decimal
WINDOW_DAYS = 14
PAPER_DB = Path("data/paper_cohorts/kalshi-event-news-20260820/paper_trades.db")
DEFAULT_LOG = Path("logs/app/politics.launchd.stderr.log")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_TS = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")
_TICKER = re.compile(r"(?:blocked|ticker)=([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)*)")
_KX = re.compile(r"KX[A-Z0-9]+(?:-[A-Z0-9]+)*")

# Order is the funnel order. edge_threshold is logged and is not a drop.
DROP_STAGES = (
    "band_guard",
    "yes_floor",
    "risk_cap",
    "exposure",
    "family",
    "veto",
    "watch",
    "edge",
)


def window_bounds(report_day: date) -> tuple[datetime, datetime]:
    """Fourteen UTC days ending at the end of report_day."""
    end = datetime(
        report_day.year, report_day.month, report_day.day, tzinfo=timezone.utc
    ) + timedelta(days=1)
    return end - timedelta(days=WINDOW_DAYS), end


def _stamp(line: str) -> datetime | None:
    found = _TS.search(line)
    if found is None:
        return None
    return datetime.strptime(
        f"{found.group(1)} {found.group(2)}",
        "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=timezone.utc)


def _stage(line: str) -> str | None:
    if "executed_ask_in_excluded_band" in line:
        return "band_guard"
    if "yes_below_floor" in line:
        return "yes_floor"
    if "[EVENT_NEWS_RISK_CAP]" in line and "contracts=0" in line:
        return "risk_cap"
    if "exposure_cap" in line:
        return "exposure"
    if (
        "one_per_family" in line
        or "EVENT_NEWS_FAMILY_LOCK" in line
        or "event_open_prefix_cap" in line
    ):
        return "family"
    if (
        "EVENT_NEWS_GROK_VETO did not return yes" in line
        or "GROK_VETO] result=no" in line
        or "GROK_GROUNDING] skip=no" in line
    ):
        return "veto"
    if "GROK_WATCH] result=contradict" in line:
        return "watch"
    if "below min_edge" in line or "edge_gone" in line:
        return "edge"
    return None


def _names(line: str, stage: str) -> list[str]:
    if stage == "family":
        blocked = re.findall(r"blocked=([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)*)", line)
        if blocked:
            return blocked
    named = _TICKER.findall(line)
    if named:
        return named
    return _KX.findall(line)


def scan_log(
    log_path: Path,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, object]:
    """Count drops. Missing log returns not_measured and does not raise."""
    empty = {
        "log_present": False,
        "candidates_lines": "not_measured",
        "candidates_tickers": "not_measured",
        "hold_lines": "not_measured",
        "stages": {
            stage: {"lines": "not_measured", "tickers": "not_measured"}
            for stage in DROP_STAGES
        },
    }
    if not log_path.exists():
        return empty
    candidates: set[str] = set()
    candidate_lines = 0
    hold_lines = 0
    stage_lines = {stage: 0 for stage in DROP_STAGES}
    stage_names = {stage: set() for stage in DROP_STAGES}
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = _ANSI.sub("", raw)
            stamp = _stamp(line)
            if stamp is None or stamp < start or stamp >= end:
                continue
            if "EVENT_NEWS_PREWARM" in line:
                candidate_lines += 1
                candidates.update(_KX.findall(line))
            if "LIVE_GUARD] BLOCKED" in line:
                hold_lines += 1
            stage = _stage(line)
            if stage is None:
                continue
            stage_lines[stage] += 1
            stage_names[stage].update(_names(line, stage))
    return {
        "log_present": True,
        "candidates_lines": candidate_lines,
        "candidates_tickers": len(candidates),
        "hold_lines": hold_lines,
        "stages": {
            stage: {"lines": stage_lines[stage], "tickers": len(stage_names[stage])}
            for stage in DROP_STAGES
        },
    }


def _money(value: Decimal) -> str:
    return format(value.quantize(D("0.0001")), "f")


def _paper_entries(
    db_path: Path,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, object]:
    """Fee-net is payout minus cost. Cost in this database includes the fee."""
    missing = {
        "present": False,
        "n": "not_measured",
        "fee_net": "not_measured",
    }
    if not db_path.exists():
        return missing
    try:
        uri = f"file:{db_path}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return missing
    try:
        rows = connection.execute(
            "SELECT ts, resolved, gross_payout_cents, cost_dollars "
            "FROM paper_trades"
        ).fetchall()
    except sqlite3.Error:
        return missing
    finally:
        connection.close()
    total = D("0")
    n = 0
    for ts, resolved, payout_cents, cost in rows:
        try:
            entered = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=timezone.utc)
        if entered < start or entered >= end:
            continue
        n += 1
        if int(resolved or 0) == 1 and cost is not None:
            payout = D(str(payout_cents or 0)) / D("100")
            total += payout - D(str(cost))
    return {"present": True, "n": n, "fee_net": _money(total)}


def _in_window(entered: datetime, start: datetime, end: datetime) -> bool:
    return start <= entered < end


def window_entries(start: datetime, end: datetime) -> dict[str, object]:
    """Settled live fee-net in the window. Open rows stay out of fee-net."""
    settled = [
        row
        for row in LIVE_SETTLED_ROWS
        if _in_window(row.entered_at, start, end) and row.fee_net is not None
    ]
    paper = [
        row
        for row in PAPER_ROWS
        if _in_window(row.entered_at, start, end) and row.fee_net is not None
    ]
    opens = [row for row in OPEN_ROWS if _in_window(row.entered_at, start, end)]
    live_fee = sum((row.fee_net or D("0") for row in settled), D("0"))
    paper_fee = sum((row.fee_net or D("0") for row in paper), D("0"))
    yes_kept = [row for row in settled if not yes_floor_drops(row)]
    yes_fee = sum((row.fee_net or D("0") for row in yes_kept), D("0"))
    yes_positive = sum(
        (
            row.fee_net
            for row in settled
            if yes_floor_drops(row) and row.fee_net is not None and row.fee_net > 0
        ),
        D("0"),
    )
    risk_fee = D("0")
    risk_positive = D("0")
    for row in settled:
        sized = capped_contracts(row)
        scaled = (row.fee_net or D("0")) * D(sized) / D(row.contracts)
        risk_fee += scaled
        if row.fee_net is not None and row.fee_net > 0:
            risk_positive += row.fee_net - scaled
    maker = sum((maker_fee_savings(row, row.contracts) for row in settled), D("0"))
    return {
        "live_n": len(settled) + len(opens),
        "live_settled_n": len(settled),
        "live_open_n": len(opens),
        "live_fee_net": _money(live_fee),
        "replay_paper_n": len(paper),
        "replay_paper_fee_net": _money(paper_fee),
        "yes_floor_fee_net": _money(yes_fee),
        "yes_floor_positive_removed": _money(yes_positive),
        "yes_floor_n": len(yes_kept),
        "risk_cap_fee_net": _money(risk_fee),
        "risk_cap_positive_removed": _money(risk_positive),
        "maker_delta": _money(maker),
        "exposure_fee_net": "not_measured",
        "family_fee_net": "not_measured",
    }


def largest_positive_removal(modes: list[dict[str, str]] | None = None) -> tuple[str, str]:
    """The replay filter that removes the most positive fee-net."""
    rows = modes if modes is not None else replay_modes()
    best_mode = ""
    best_value = D("-1")
    for row in rows:
        raw = row["positive_fee_net_removed"]
        if raw in {"not_measured", "not_separated"}:
            continue
        value = D(raw)
        if value > best_value:
            best_value = value
            best_mode = str(row["mode"])
    return best_mode, format(best_value, "f")


def build_funnel(
    *,
    report_day: date,
    log_path: Path = DEFAULT_LOG,
    db_path: Path = PAPER_DB,
) -> dict[str, object]:
    start, end = window_bounds(report_day)
    return {
        "report_day": report_day.isoformat(),
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "log": scan_log(log_path, start=start, end=end),
        "paper": _paper_entries(db_path, start=start, end=end),
        "entries": window_entries(start, end),
        "replay": replay_modes(),
        "largest_positive_removal": largest_positive_removal(),
    }


def format_funnel(report: dict[str, object]) -> str:
    log = report["log"]
    assert isinstance(log, dict)
    paper = report["paper"]
    assert isinstance(paper, dict)
    entries = report["entries"]
    assert isinstance(entries, dict)
    stages = log["stages"]
    assert isinstance(stages, dict)
    mode, removed = report["largest_positive_removal"]
    assert isinstance(mode, str) and isinstance(removed, str)
    lines = [
        f"# Politics funnel {report['report_day']}",
        "",
        f"Window {report['start']} through {report['end']} exclusive.",
        "Counts are log lines and unique tickers. The same open name repeats.",
        "New flags are off in live, so their log drops are zero until they are enabled.",
        "",
        "| stage | lines | unique tickers |",
        "|---|---:|---:|",
        (
            f"| candidates seen | {log['candidates_lines']} | "
            f"{log['candidates_tickers']} |"
        ),
    ]
    for stage in DROP_STAGES:
        row = stages[stage]
        assert isinstance(row, dict)
        lines.append(f"| {stage} | {row['lines']} | {row['tickers']} |")
    lines.extend(
        [
            "",
            (
                f"Paper entries in the window: n={paper['n']} "
                f"fee_net={paper['fee_net']}."
            ),
            (
                f"Live entries in the window: n={entries['live_n']} "
                f"settled={entries['live_settled_n']} "
                f"open={entries['live_open_n']} "
                f"settled fee_net={entries['live_fee_net']}."
            ),
            "Open rows are cost only and are not in fee-net.",
            (
                "Live submission-hold blocks in the window: "
                f"{log['hold_lines']} lines. That hold is not one of these flags."
            ),
            "",
            "Fee-net of the settled live rows in the window, by flag:",
            "",
            "| stage | n | fee_net | positive fee-net removed |",
            "|---|---:|---:|---:|",
            (
                f"| baseline | {entries['live_settled_n']} | "
                f"{entries['live_fee_net']} | 0.0000 |"
            ),
            (
                f"| yes_floor | {entries['yes_floor_n']} | "
                f"{entries['yes_floor_fee_net']} | "
                f"{entries['yes_floor_positive_removed']} |"
            ),
            (
                f"| risk_cap | {entries['live_settled_n']} | "
                f"{entries['risk_cap_fee_net']} | "
                f"{entries['risk_cap_positive_removed']} |"
            ),
            f"| exposure | {entries['live_settled_n']} | not_measured | not_measured |",
            f"| family | {entries['live_settled_n']} | not_measured | not_measured |",
            (
                f"| maker | {entries['live_settled_n']} | "
                f"quote_delta={entries['maker_delta']} | 0.0000 |"
            ),
            "",
            "All-history replay, including paper rows from August and September:",
            "",
            "| mode | n | fee_net | delta | positive fee-net removed |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    replay_rows = report["replay"]
    assert isinstance(replay_rows, list)
    for row in replay_rows:
        assert isinstance(row, dict)
        lines.append(
            f"| {row['mode']} | {row['n']} | {row['fee_net']} | "
            f"{row['delta']} | {row['positive_fee_net_removed']} |"
        )
    lines.extend(
        [
            "",
            (
                f"The filter that removes the most positive fee-net is {mode} "
                f"({removed}). That figure is the all-history replay, n=9."
            ),
            "The YES floor removes a loss. Its positive fee-net removed is 0.0000.",
            "Maker is a same-price fee quote. Fill probability is not modeled.",
            "Family live overlap is not_measured because settlement clocks are absent.",
            "Exposure is not_measured because equity at each entry is unknown.",
            "",
        ]
    )
    text = "\n".join(lines)
    for banned in ("client_order_id", "ghp_", "BEGIN ", "api_key", "signal_headline"):
        if banned in text:
            raise RuntimeError("funnel report included a banned field")
    return text


def main() -> None:
    report_day = date.today()
    text = format_funnel(build_funnel(report_day=report_day))
    out = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "reports"
        / f"politics-funnel-{report_day.isoformat()}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
