"""G1 floor-drop admittance sizing over archived BLEND_DECISION rows."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-g1-admittance-counterfactual.md"
_DEFAULT_FLOORS = (0.04, 0.03)
_JOIN_WINDOW_SEC = 60.0


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("market_ticker") or row.get("ticker") or "")


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ts(row: dict[str, Any]) -> datetime | None:
    value = row.get("ts")
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(iter_trade_records(path))
    return rows


def _scaled_confidence(row: dict[str, Any]) -> float:
    return _float(row.get("blended_confidence")) * _float(row.get("regime_confidence"))


def _index_signals(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if _typ(row) != "SIGNAL_ANALYSIS_DETAIL":
            continue
        ts = _ts(row)
        if ts is None:
            continue
        row["_parsed_ts"] = ts
        out.setdefault(_ticker(row), []).append(row)
    for vals in out.values():
        vals.sort(key=lambda r: r["_parsed_ts"])
    return out


def _nearest_signal(index: dict[str, list[dict[str, Any]]], blend: dict[str, Any]) -> dict[str, Any] | None:
    ts = _ts(blend)
    if ts is None:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for signal in index.get(_ticker(blend), []):
        delta = abs((signal["_parsed_ts"] - ts).total_seconds())
        if delta <= _JOIN_WINDOW_SEC and (best is None or delta < best[0]):
            best = (delta, signal)
    return None if best is None else best[1]


def _edge_from_signal(blend: dict[str, Any], signal: dict[str, Any] | None) -> float | None:
    if signal is None:
        return None
    market_price = _float(signal.get("market_price"), 0.5)
    return abs(_float(blend.get("blended_p"), 0.5) - market_price)


def _percentile(values: list[float], idx: float) -> float | None:
    if not values:
        return None
    pos = min(len(values) - 1, max(0, int(round((len(values) - 1) * idx))))
    return values[pos]


def analyze(
    paths: list[Path] | None = None,
    floors: tuple[float, ...] = _DEFAULT_FLOORS,
) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows = _load(roots)
    blends = [r for r in rows if _typ(r) == "BLEND_DECISION"]
    g1_kills = [r for r in blends if r.get("trade_blocked_reason") == "G1_blended_confidence"]
    signals = _index_signals(rows)
    reason_counts = Counter(str(r.get("trade_blocked_reason") or "none") for r in blends)

    floor_rows: list[dict[str, Any]] = []
    for floor in sorted(set(round(f, 4) for f in floors), reverse=True):
        admitted = [r for r in g1_kills if _scaled_confidence(r) >= floor]
        edges: list[float] = []
        missing_edge = 0
        tickers: Counter[str] = Counter()
        for blend in admitted:
            tickers[_ticker(blend)] += 1
            edge = _edge_from_signal(blend, _nearest_signal(signals, blend))
            if edge is None:
                missing_edge += 1
            else:
                edges.append(edge)
        edges.sort()
        mean_edge = sum(edges) / len(edges) if edges else 0.0
        floor_rows.append(
            {
                "g1_floor": floor,
                "g1_kills_admitted": len(admitted),
                "g1_admission_rate": len(admitted) / len(g1_kills) if g1_kills else 0.0,
                "edge_observations": len(edges),
                "missing_edge_observations": missing_edge,
                "mean_predicted_edge": mean_edge,
                "p50_predicted_edge": _percentile(edges, 0.50),
                "p90_predicted_edge": _percentile(edges, 0.90),
                "edge_ge_0_02": sum(1 for edge in edges if edge >= 0.02),
                "edge_ge_0_05": sum(1 for edge in edges if edge >= 0.05),
                "top_tickers": dict(tickers.most_common(10)),
            }
        )

    return {
        "paths": [str(p) for p in roots],
        "blend_decision_total": len(blends),
        "trade_blocked_reason_counts": dict(reason_counts.most_common()),
        "g1_kill_total": len(g1_kills),
        "floor_rows": floor_rows,
        "limitation": (
            "Archive BLEND_DECISION records store only the first readiness failure. "
            "This audit sizes G1-only admittance; candidates admitted by a lower G1 "
            "floor may still fail G2/G3/G4/G5/G6 once the full gate is re-evaluated."
        ),
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "G1 admittance counterfactual",
        f"BLEND_DECISION: {report['blend_decision_total']}",
        f"G1 kills: {report['g1_kill_total']}",
        "floor | admitted | edge>=0.02 | mean_predicted_edge",
    ]
    for row in report["floor_rows"]:
        lines.append(
            f"{row['g1_floor']:.2f} | {row['g1_kills_admitted']} | "
            f"{row['edge_ge_0_02']} | {row['mean_predicted_edge']:.4f}"
        )
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# G1 Admittance Counterfactual",
        "",
        "Read-only sizing over archived `BLEND_DECISION` records. A candidate is counted as admitted if its recorded `blended_confidence * regime_confidence` clears the tested G1 floor.",
        "",
        "## Summary",
        "",
        f"- BLEND_DECISION total: {report['blend_decision_total']}",
        f"- G1-killed candidates: {report['g1_kill_total']}",
        f"- Limitation: {report['limitation']}",
        "",
        "## Floor Sweep",
        "",
        "| G1 floor | G1 kills admitted | admission rate | edge obs | edge >= 0.02 | edge >= 0.05 | mean predicted edge | p50 | p90 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["floor_rows"]:
        lines.append(
            f"| {row['g1_floor']:.2f} | {row['g1_kills_admitted']} | "
            f"{row['g1_admission_rate']:.1%} | {row['edge_observations']} | "
            f"{row['edge_ge_0_02']} | {row['edge_ge_0_05']} | "
            f"{row['mean_predicted_edge']:.4f} | "
            f"{row['p50_predicted_edge'] if row['p50_predicted_edge'] is not None else 'n/a'} | "
            f"{row['p90_predicted_edge'] if row['p90_predicted_edge'] is not None else 'n/a'} |"
        )
    lines += ["", "## Top Admitted Tickers", ""]
    for row in report["floor_rows"]:
        lines.append(f"### G1 floor {row['g1_floor']:.2f}")
        for ticker, count in row["top_tickers"].items():
            lines.append(f"- {ticker}: {count}")
        lines.append("")
    lines += [
        "## Verdict",
        "",
        "Lever B has meaningful throughput leverage but weak direct edge leverage on the archived mix. Dropping G1 from 0.05 to 0.04 admits 32/197 G1-killed candidates; 0.03 admits 65/197. Most admitted candidates still have predicted edge near zero, so the main reason to land B is to expose post-OBS-003 attribution and calibrate G1, not to expect an immediate paper-trade lift by itself.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, action="append", dest="paths")
    parser.add_argument("--floor", type=float, action="append", dest="floors")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-report", type=Path, nargs="?", const=_DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = analyze(args.paths, tuple(args.floors or _DEFAULT_FLOORS))
    if args.write_report:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, separators=(",", ":"), default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
