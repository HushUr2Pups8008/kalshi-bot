#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote


EDGE_BUCKETS: tuple[tuple[str, float | None, float | None], ...] = (
    ("lt_0.00", None, 0.0),
    ("0.00_to_0.05", 0.0, 0.05),
    ("0.05_to_0.10", 0.05, 0.10),
    ("gte_0.10", 0.10, None),
)


@dataclass(frozen=True)
class PaperExpectationSummary:
    total_trades: int
    resolved_trades: int
    open_trades: int
    wins: int
    losses: int
    net_pnl: float
    expectancy_per_resolved_trade: float | None
    avg_edge: float | None
    win_rate: float | None
    max_drawdown_pct: float | None
    edge_buckets: dict[str, dict[str, float | int | None]]
    by_venue: dict[str, "PaperExpectationSummary"]


@dataclass(frozen=True)
class ReplayEvidenceSummary:
    source: str
    status: Literal["scored", "insufficient_corpus", "missing", "unknown"]
    trade_count: int | None
    win_rate: float | None
    realized_pnl: float | None
    per_trade_ev: float | None
    ev_ci_95_lo: float | None
    ev_ci_95_hi: float | None

    def with_updates(self, **updates: Any) -> "ReplayEvidenceSummary":
        return replace(self, **updates)


@dataclass(frozen=True)
class ReadinessVerdict:
    ready: bool
    label: str
    reasons: list[str]


@dataclass(frozen=True)
class ProfitEvidenceReport:
    paper: PaperExpectationSummary
    replay: list[ReplayEvidenceSummary]
    verdict: ReadinessVerdict


def summarize_paper_expectancy(paper_db: Path | str) -> PaperExpectationSummary:
    rows = _load_paper_rows(Path(paper_db))
    return _summarize_rows(rows, include_by_venue=True)


def collect_replay_evidence(edge_replay_root: Path | str) -> list[ReplayEvidenceSummary]:
    root = Path(edge_replay_root)
    patterns = (
        "**/counterfactual_scores*.json",
        "ci_runs/*/verdict.json",
        "ci_runs/*/rule4_table.json",
    )
    paths: list[Path] = []
    if root.exists():
        for pattern in patterns:
            paths.extend(root.glob(pattern))
    paths = sorted({path for path in paths if path.is_file()})
    if not paths:
        return [
            ReplayEvidenceSummary(
                source=str(root),
                status="missing",
                trade_count=None,
                win_rate=None,
                realized_pnl=None,
                per_trade_ev=None,
                ev_ci_95_lo=None,
                ev_ci_95_hi=None,
            )
        ]
    return [_parse_replay_artifact(path, root) for path in paths]


def build_profit_evidence_report(
    paper_db: Path | str,
    edge_replay_root: Path | str,
) -> ProfitEvidenceReport:
    paper = summarize_paper_expectancy(paper_db)
    replay = collect_replay_evidence(edge_replay_root)
    verdict = readiness_verdict(paper, replay)
    return ProfitEvidenceReport(paper=paper, replay=replay, verdict=verdict)


def readiness_verdict(
    paper: PaperExpectationSummary,
    replay: list[ReplayEvidenceSummary],
    *,
    min_resolved_trades: int = 20,
    min_win_rate: float = 0.52,
    max_drawdown_pct: float = 0.20,
) -> ReadinessVerdict:
    reasons: list[str] = []
    if paper.resolved_trades < min_resolved_trades:
        reasons.append(
            f"resolved sample {paper.resolved_trades} below {min_resolved_trades}"
        )
    if paper.win_rate is None:
        reasons.append("paper win rate missing")
    elif paper.win_rate < min_win_rate:
        reasons.append(
            f"paper win rate {_fmt_pct(paper.win_rate)} below {_fmt_pct(min_win_rate)}"
        )
    if (
        paper.expectancy_per_resolved_trade is None
        or paper.expectancy_per_resolved_trade <= 0
    ):
        reasons.append("paper expectancy not positive")
    if paper.max_drawdown_pct is not None and paper.max_drawdown_pct > max_drawdown_pct:
        reasons.append(
            f"drawdown {_fmt_pct(paper.max_drawdown_pct)} above {_fmt_pct(max_drawdown_pct)}"
        )

    current_replay = _current_replay_items(replay)
    scored = [item for item in current_replay if item.status == "scored"]
    if current_replay and not scored:
        reasons.append("missing current replay evidence")
    elif not scored:
        reasons.append("missing replay evidence")
    elif not _replay_passes(scored[-1]):
        reasons.append("replay EV evidence failed")

    ready = not reasons
    return ReadinessVerdict(
        ready=ready,
        label="live-ready" if ready else "not live-ready",
        reasons=reasons,
    )


def render_json(report: ProfitEvidenceReport) -> str:
    return json.dumps(
        {
            "paper_expectancy": _paper_to_dict(report.paper),
            "replay_evidence": [_replay_to_dict(item) for item in report.replay],
            "readiness_verdict": _verdict_to_dict(report.verdict),
        },
        indent=2,
        sort_keys=True,
    )


def render_text(report: ProfitEvidenceReport) -> str:
    paper = report.paper
    lines = [
        "PAPER EXPECTANCY",
        f"  total trades: {paper.total_trades}",
        f"  resolved/open: {paper.resolved_trades}/{paper.open_trades}",
        f"  win rate: {_fmt_optional_pct(paper.win_rate)}",
        f"  net P&L: {_fmt_money(paper.net_pnl)}",
        "  expectancy/resolved trade: "
        f"{_fmt_optional_money(paper.expectancy_per_resolved_trade)}",
        f"  avg stored edge: {_fmt_optional_pct(paper.avg_edge)}",
        f"  max drawdown: {_fmt_optional_pct(paper.max_drawdown_pct)}",
        "",
        "REPLAY EVIDENCE",
    ]
    for item in report.replay:
        lines.append(
            "  "
            f"{item.source}: {item.status}"
            f" trades={_fmt_optional_int(item.trade_count)}"
            f" win_rate={_fmt_optional_pct(item.win_rate)}"
            f" ev={_fmt_optional_money(item.per_trade_ev)}"
            f" ci95=[{_fmt_optional_money(item.ev_ci_95_lo)}, "
            f"{_fmt_optional_money(item.ev_ci_95_hi)}]"
        )
    lines.extend(["", "READINESS VERDICT", f"  {report.verdict.label}"])
    for reason in report.verdict.reasons:
        lines.append(f"  - {reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-db", required=True, type=Path)
    parser.add_argument("--edge-replay-root", required=True, type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    report = build_profit_evidence_report(args.paper_db, args.edge_replay_root)
    if args.as_json:
        print(render_json(report))
    else:
        print(render_text(report))
    return 0


def _load_paper_rows(paper_db: Path) -> list[dict[str, Any]]:
    uri = f"file:{quote(str(paper_db.resolve()))}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    trade_id,
                    edge,
                    resolved,
                    pnl_dollars,
                    notional_bankroll_before,
                    notional_bankroll_after,
                    COALESCE(venue, 'unknown') AS venue
                FROM paper_trades
                """
            )
        ]


def _summarize_rows(
    rows: list[dict[str, Any]],
    *,
    include_by_venue: bool,
) -> PaperExpectationSummary:
    total = len(rows)
    resolved_rows = [row for row in rows if _as_bool(row.get("resolved"))]
    open_trades = total - len(resolved_rows)
    pnl_values = [_as_float(row.get("pnl_dollars")) or 0.0 for row in resolved_rows]
    wins = sum(1 for pnl in pnl_values if pnl > 0)
    losses = sum(1 for pnl in pnl_values if pnl < 0)
    net_pnl = sum(pnl_values)
    edges = [_as_float(row.get("edge")) for row in rows if _as_float(row.get("edge")) is not None]
    by_venue: dict[str, PaperExpectationSummary] = {}
    if include_by_venue:
        venues = sorted({str(row.get("venue") or "unknown") for row in rows})
        by_venue = {
            venue: _summarize_rows(
                [row for row in rows if str(row.get("venue") or "unknown") == venue],
                include_by_venue=False,
            )
            for venue in venues
        }
    return PaperExpectationSummary(
        total_trades=total,
        resolved_trades=len(resolved_rows),
        open_trades=open_trades,
        wins=wins,
        losses=losses,
        net_pnl=net_pnl,
        expectancy_per_resolved_trade=(
            net_pnl / len(resolved_rows) if resolved_rows else None
        ),
        avg_edge=(sum(edges) / len(edges) if edges else None),
        win_rate=(wins / len(resolved_rows) if resolved_rows else None),
        max_drawdown_pct=_max_drawdown_pct(rows),
        edge_buckets=_edge_bucket_summary(rows),
        by_venue=by_venue,
    )


def _edge_bucket_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int | None]]:
    result: dict[str, dict[str, float | int | None]] = {}
    for name, lo, hi in EDGE_BUCKETS:
        bucket_rows = [
            row
            for row in rows
            if (edge := _as_float(row.get("edge"))) is not None
            and (lo is None or edge >= lo)
            and (hi is None or edge < hi)
        ]
        resolved_rows = [row for row in bucket_rows if _as_bool(row.get("resolved"))]
        pnl = sum((_as_float(row.get("pnl_dollars")) or 0.0) for row in resolved_rows)
        edges = [
            _as_float(row.get("edge"))
            for row in bucket_rows
            if _as_float(row.get("edge")) is not None
        ]
        result[name] = {
            "total_trades": len(bucket_rows),
            "resolved_trades": len(resolved_rows),
            "net_pnl": pnl,
            "realized_edge": pnl / len(resolved_rows) if resolved_rows else None,
            "avg_edge": sum(edges) / len(edges) if edges else None,
        }
    return result


def _max_drawdown_pct(rows: list[dict[str, Any]]) -> float | None:
    values: list[float] = []
    for row in rows:
        before = _as_float(row.get("notional_bankroll_before"))
        after = _as_float(row.get("notional_bankroll_after"))
        if before is not None:
            values.append(before)
        if after is not None:
            values.append(after)
    if not values:
        return None
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    return max_drawdown


def _parse_replay_artifact(path: Path, root: Path) -> ReplayEvidenceSummary:
    source = _relative_source(path, root)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return _replay_unknown(source)
    if data is None or data == [] or data == {}:
        return _replay_insufficient(source)
    if isinstance(data, dict) and _is_insufficient_verdict(data):
        return _replay_insufficient(source)

    metrics = _extract_replay_metrics(data)
    trade_count = _as_int(_first_present(metrics, "trade_count", "trades"))
    win_rate = _as_float(metrics.get("win_rate"))
    realized_pnl = _as_float(_first_present(metrics, "realized_pnl", "pnl"))
    per_trade_ev = _as_float(
        _first_present(metrics, "per_trade_ev", "avg_pnl_per_trade")
    )
    ci_lo, ci_hi = _extract_ci(metrics)
    has_score = any(
        value is not None
        for value in (trade_count, win_rate, realized_pnl, per_trade_ev, ci_lo, ci_hi)
    )
    if trade_count == 0:
        return _replay_insufficient(source)
    return ReplayEvidenceSummary(
        source=source,
        status="scored" if has_score else "unknown",
        trade_count=trade_count,
        win_rate=win_rate,
        realized_pnl=realized_pnl,
        per_trade_ev=per_trade_ev,
        ev_ci_95_lo=ci_lo,
        ev_ci_95_hi=ci_hi,
    )


def _extract_replay_metrics(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        summary = data.get("summary")
        if isinstance(summary, dict) and isinstance(summary.get("overall"), dict):
            return summary["overall"]
        if isinstance(data.get("overall"), dict):
            return data["overall"]
        if isinstance(data.get("rule4"), dict):
            return data["rule4"]
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return {}


def _extract_ci(metrics: dict[str, Any]) -> tuple[float | None, float | None]:
    direct_lo = _as_float(metrics.get("ev_ci_95_lo"))
    direct_hi = _as_float(metrics.get("ev_ci_95_hi"))
    if direct_lo is not None or direct_hi is not None:
        return direct_lo, direct_hi
    for key in ("ci_95", "ci95_avg_pnl"):
        value = metrics.get(key)
        if isinstance(value, list | tuple) and len(value) >= 2:
            return _as_float(value[0]), _as_float(value[1])
    return None, None


def _is_insufficient_verdict(data: dict[str, Any]) -> bool:
    text = " ".join(
        str(data.get(key, ""))
        for key in ("status", "failure_reason", "reason", "notes", "verdict")
    ).lower()
    return "insufficient" in text and "corpus" in text


def _replay_passes(item: ReplayEvidenceSummary) -> bool:
    if item.status != "scored":
        return False
    if item.per_trade_ev is None or item.per_trade_ev <= 0:
        return False
    if item.ev_ci_95_lo is not None and item.ev_ci_95_lo < 0:
        return False
    return True


def _current_replay_items(
    replay: list[ReplayEvidenceSummary],
) -> list[ReplayEvidenceSummary]:
    head_items = [item for item in replay if item.source.startswith("ci_runs/HEAD/")]
    if head_items:
        return head_items
    return [item for item in replay if item.status == "scored"]


def _replay_insufficient(source: str) -> ReplayEvidenceSummary:
    return ReplayEvidenceSummary(source, "insufficient_corpus", None, None, None, None, None, None)


def _replay_unknown(source: str) -> ReplayEvidenceSummary:
    return ReplayEvidenceSummary(source, "unknown", None, None, None, None, None, None)


def _relative_source(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _paper_to_dict(summary: PaperExpectationSummary) -> dict[str, Any]:
    return {
        "total_trades": summary.total_trades,
        "resolved_trades": summary.resolved_trades,
        "open_trades": summary.open_trades,
        "wins": summary.wins,
        "losses": summary.losses,
        "net_pnl": summary.net_pnl,
        "expectancy_per_resolved_trade": summary.expectancy_per_resolved_trade,
        "avg_edge": summary.avg_edge,
        "win_rate": summary.win_rate,
        "max_drawdown_pct": summary.max_drawdown_pct,
        "edge_buckets": summary.edge_buckets,
        "by_venue": {
            venue: _paper_to_dict(venue_summary)
            for venue, venue_summary in summary.by_venue.items()
        },
    }


def _replay_to_dict(summary: ReplayEvidenceSummary) -> dict[str, Any]:
    return {
        "source": summary.source,
        "status": summary.status,
        "trade_count": summary.trade_count,
        "win_rate": summary.win_rate,
        "realized_pnl": summary.realized_pnl,
        "per_trade_ev": summary.per_trade_ev,
        "ev_ci_95_lo": summary.ev_ci_95_lo,
        "ev_ci_95_hi": summary.ev_ci_95_hi,
    }


def _verdict_to_dict(verdict: ReadinessVerdict) -> dict[str, Any]:
    return {"ready": verdict.ready, "label": verdict.label, "reasons": verdict.reasons}


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _as_bool(value: Any) -> bool:
    return bool(value)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value: float) -> str:
    return f"{value:.1%}"


def _fmt_optional_pct(value: float | None) -> str:
    return "n/a" if value is None else _fmt_pct(value)


def _fmt_money(value: float) -> str:
    return f"${value:+.2f}"


def _fmt_optional_money(value: float | None) -> str:
    return "n/a" if value is None else _fmt_money(value)


def _fmt_optional_int(value: int | None) -> str:
    return "n/a" if value is None else str(value)


if __name__ == "__main__":
    raise SystemExit(main())
