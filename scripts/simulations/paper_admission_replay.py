"""Replay paper-trade admission candidates under bounded diagnostic toggles.

Read-only. Consumes trade JSONL records and reports how many recorded
BLEND_DECISION candidates would be admitted under paper-only counterfactuals.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.dossier_builder import half_life_for_regime, recency_score  # noqa: E402
from tasks.trade_readiness_gate import G2_MIN_SOURCE_CLASSES, G6_RECENCY_THRESHOLD  # noqa: E402
from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS: tuple[Path, ...] = (
    _REPO_ROOT / "logs" / "trades" / "live" / "trades.jsonl",
    *sorted((_REPO_ROOT / "logs" / "trades" / "archive" / "2026" / "05").glob("*.jsonl")),
)
_JOIN_WINDOW_SECONDS = 120.0
_UNKNOWN_REGIME = {"fast": 1 / 3, "interpretation": 1 / 3, "structural": 1 / 3}


@dataclass(frozen=True)
class ReplayConfig:
    include_trigger_evidence: bool = False
    unknown_regime_default: str = "fast"
    paper_min_edge: float = 0.02


def _typ(record: dict[str, Any]) -> str:
    return str(record.get("type") or record.get("record_type") or "")


def _ticker(record: dict[str, Any]) -> str:
    return str(record.get("market_ticker") or record.get("ticker") or "")


def _ts(record: dict[str, Any]) -> datetime | None:
    value = record.get("ts")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _normalize_reason(reason: Any) -> str | None:
    if reason is None:
        return None
    text = str(reason).strip()
    if not text:
        return None
    if text.startswith("edge ") and "below min_edge" in text:
        return "executor_min_edge"
    return text


def _append_reason(reasons: list[str], reason: Any) -> None:
    normalized = _normalize_reason(reason)
    if normalized and normalized not in {"passed", "none"} and normalized not in reasons:
        reasons.append(normalized)


def _load_records(paths: Iterable[Path], since: str | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        for record in iter_trade_records(path):
            if since:
                ts = str(record.get("ts") or "")
                if ts and ts[:10] < since:
                    continue
            records.append(record)
    records.sort(key=lambda r: str(r.get("ts") or ""))
    return records


def _nearest(
    records: list[dict[str, Any]],
    blend: dict[str, Any],
    record_type: str,
) -> dict[str, Any] | None:
    blend_ts = _ts(blend)
    blend_ticker = _ticker(blend)
    best: tuple[float, dict[str, Any]] | None = None
    for record in records:
        if _typ(record) != record_type or _ticker(record) != blend_ticker:
            continue
        record_ts = _ts(record)
        if blend_ts is None or record_ts is None:
            delta = 0.0 if best is None else _JOIN_WINDOW_SECONDS + 1
        else:
            delta = abs((record_ts - blend_ts).total_seconds())
        if delta <= _JOIN_WINDOW_SECONDS and (best is None or delta < best[0]):
            best = (delta, record)
    return None if best is None else best[1]


def _first_present(*records: dict[str, Any] | None, key: str) -> Any:
    for record in records:
        if record is not None and key in record:
            return record.get(key)
    return None


def _list_field(*records: dict[str, Any] | None, keys: tuple[str, ...]) -> list[Any]:
    out: list[Any] = []
    for record in records:
        if record is None:
            continue
        for key in keys:
            value = record.get(key)
            if isinstance(value, list):
                out.extend(value)
            elif value is not None:
                out.append(value)
    return out


def _signal_meta(skipped: dict[str, Any] | None) -> dict[str, Any]:
    meta = (skipped or {}).get("signal_meta")
    return meta if isinstance(meta, dict) else {}


def _evidence_items(*records: dict[str, Any] | None) -> list[tuple[float, str]]:
    items: list[tuple[float, str]] = []
    for record in records:
        if record is None:
            continue
        raw_items = record.get("evidence_items") or record.get("recent_records") or []
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            weight = _float(item.get("original_weight") or item.get("weight"))
            ts = item.get("ingested_ts") or item.get("ts")
            if weight is not None and isinstance(ts, str) and ts:
                items.append((weight, ts))
    return items


def _trigger_item(skipped: dict[str, Any] | None) -> tuple[float, str] | None:
    if skipped is None:
        return None
    meta = _signal_meta(skipped)
    weight = _float(
        meta.get("trigger_evidence_original_weight")
        or meta.get("trigger_original_weight")
        or meta.get("original_weight")
    )
    ts = (
        meta.get("trigger_evidence_ingested_ts")
        or meta.get("trigger_ingested_ts")
        or meta.get("ingested_ts")
        or skipped.get("trigger_ingested_ts")
    )
    if weight is None or not isinstance(ts, str) or not ts:
        return None
    return (weight, ts)


def _edge(blend: dict[str, Any], skipped: dict[str, Any] | None) -> float | None:
    explicit = _float(_first_present(skipped, blend, key="edge"))
    if explicit is not None:
        return abs(explicit)
    model_probability = _float(_first_present(skipped, blend, key="model_probability"))
    market_price = _float(_first_present(skipped, blend, key="market_price"))
    if model_probability is None or market_price is None:
        return None
    return abs(model_probability - market_price / 100.0)


def build_candidates(records: list[dict[str, Any]], *, limit: int | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    blends = [record for record in records if _typ(record) == "BLEND_DECISION"]
    for blend in blends:
        skipped = _nearest(records, blend, "SKIPPED")
        gate = _nearest(records, blend, "GATE_SUMMARY")
        meta = _signal_meta(skipped)
        reasons = _candidate_reasons(skipped=skipped, blend=blend, gate=gate)
        classes = [
            str(value)
            for value in _list_field(
                blend,
                skipped,
                keys=("evidence_source_classes", "source_classes"),
            )
            if value
        ]
        trigger_source_class = (
            meta.get("trigger_evidence_source_class")
            or meta.get("trigger_source_class")
            or meta.get("source_class")
            or (skipped or {}).get("source_class")
            or (skipped or {}).get("news_class")
        )
        candidates.append(
            {
                "ticker": _ticker(blend),
                "ts": blend.get("ts"),
                "recorded_reason": reasons[0] if reasons else None,
                "recorded_reasons": reasons,
                "edge": _edge(blend, skipped),
                "evidence_source_classes": classes,
                "trigger_source_class": str(trigger_source_class) if trigger_source_class else None,
                "evidence_items": _evidence_items(blend, skipped),
                "trigger_item": _trigger_item(skipped),
                "recency_score": _float(_first_present(blend, skipped, key="recency_score")),
                "regime_weights": blend.get("regime_weights") or {},
                "gate_binding_constraint": (gate or {}).get("binding_constraint"),
            }
        )
    return candidates[-limit:] if limit else candidates


def _candidate_reasons(
    *,
    skipped: dict[str, Any] | None,
    blend: dict[str, Any],
    gate: dict[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    _append_reason(reasons, _first_present(skipped, blend, key="reason"))
    _append_reason(reasons, _first_present(blend, key="trade_blocked_reason"))
    _append_reason(reasons, _first_present(gate, key="binding_constraint"))
    if gate:
        for item in gate.get("gate_chain") or []:
            if not isinstance(item, str) or "FAIL" not in item:
                continue
            _append_reason(reasons, item.split(":", 1)[0])
    return reasons


def _unknown_regime(regime_weights: Any) -> bool:
    if not isinstance(regime_weights, dict) or not regime_weights:
        return True
    keys = {"fast", "interpretation", "structural"}
    values = {key: round(float(regime_weights.get(key, 0.0)), 4) for key in keys}
    return all(abs(values[key] - _UNKNOWN_REGIME[key]) < 0.0002 for key in keys)


def _dominant_regime(candidate: dict[str, Any], config: ReplayConfig) -> str:
    weights = candidate.get("regime_weights")
    if _unknown_regime(weights):
        return config.unknown_regime_default
    return max(weights, key=weights.get)


def _recency(candidate: dict[str, Any], config: ReplayConfig) -> float | None:
    items = list(candidate.get("evidence_items") or [])
    if config.include_trigger_evidence and candidate.get("trigger_item") is not None:
        items.append(candidate["trigger_item"])
    now = _ts(candidate)
    if not items or now is None:
        return _float(candidate.get("recency_score"))
    return recency_score(items, now, half_life_for_regime(_dominant_regime(candidate, config)))


def replay_candidate(candidate: dict[str, Any], config: ReplayConfig) -> dict[str, Any]:
    reasons = []
    for reason in candidate.get("recorded_reasons") or [candidate.get("recorded_reason")]:
        _append_reason(reasons, reason)
    original_reasons = list(reasons)

    classes = list(candidate.get("evidence_source_classes") or [])
    if config.include_trigger_evidence and candidate.get("trigger_source_class"):
        classes.append(candidate["trigger_source_class"])
    if "G2_evidence_source_class_diversity" in reasons and len(set(classes)) >= G2_MIN_SOURCE_CLASSES:
        reasons.remove("G2_evidence_source_class_diversity")

    score = _recency(candidate, config)
    if (
        "G6_recency_score" in reasons
        and score is not None
        and score >= G6_RECENCY_THRESHOLD
    ):
        reasons.remove("G6_recency_score")

    edge = _float(candidate.get("edge"))
    if not reasons and (edge is None or edge < config.paper_min_edge):
        reasons.append("executor_min_edge")
    if "executor_min_edge" in reasons and edge is not None and edge >= config.paper_min_edge:
        reasons.remove("executor_min_edge")

    cleared = [reason for reason in original_reasons if reason not in reasons]
    return {
        "ticker": candidate["ticker"],
        "ts": candidate.get("ts"),
        "admitted": not reasons,
        "first_reason": reasons[0] if reasons else None,
        "reasons": reasons,
        "cleared_reasons": cleared,
        "edge": edge,
        "recency_score": score,
        "source_class_count": len(set(classes)),
    }


def _scenario_configs() -> dict[str, ReplayConfig]:
    return {
        "baseline": ReplayConfig(),
        "trigger_evidence": ReplayConfig(include_trigger_evidence=True),
        "unknown_interpretation": ReplayConfig(unknown_regime_default="interpretation"),
        "paper_min_edge_0_01": ReplayConfig(paper_min_edge=0.01),
        "trigger_plus_interpretation": ReplayConfig(
            include_trigger_evidence=True,
            unknown_regime_default="interpretation",
        ),
        "combined": ReplayConfig(
            include_trigger_evidence=True,
            unknown_regime_default="interpretation",
            paper_min_edge=0.01,
        ),
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = Counter(result["first_reason"] or "ADMITTED" for result in results)
    return {
        "admitted": reasons.pop("ADMITTED", 0),
        "blocked": sum(reasons.values()),
        "reason_counts": dict(reasons),
    }


def analyze(
    paths: Iterable[Path] | None = None,
    *,
    since: str | None = None,
    limit: int | None = 6,
) -> dict[str, Any]:
    path_list = list(paths or _DEFAULT_PATHS)
    records = _load_records(path_list, since=since)
    candidates = build_candidates(records, limit=limit)
    scenario_results = {
        name: [replay_candidate(candidate, config) for candidate in candidates]
        for name, config in _scenario_configs().items()
    }
    scenarios = {name: _summarize(results) for name, results in scenario_results.items()}
    baseline_counts = Counter(scenarios["baseline"]["reason_counts"])
    reason_deltas: dict[str, dict[str, int]] = {}
    for name, summary in scenarios.items():
        if name == "baseline":
            continue
        counts = Counter(summary["reason_counts"])
        keys = set(baseline_counts) | set(counts)
        reason_deltas[name] = {
            key: counts.get(key, 0) - baseline_counts.get(key, 0)
            for key in sorted(keys)
            if counts.get(key, 0) != baseline_counts.get(key, 0)
        }
    return {
        "paths_scanned_count": len(path_list),
        "paths_scanned_sample": _path_sample(path_list),
        "since": since,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "scenarios": scenarios,
        "reason_deltas": reason_deltas,
    }


def _path_sample(paths: list[Path]) -> list[str]:
    if len(paths) <= 5:
        return [str(path) for path in paths]
    return [str(paths[0]), str(paths[1]), "...", str(paths[-1])]


def render(report: dict[str, Any]) -> str:
    lines = [
        "Paper admission replay",
        f"candidates: {report['candidate_count']}",
        "scenario | admitted | blocked | reasons",
        "--- | ---: | ---: | ---",
    ]
    for name, summary in report["scenarios"].items():
        reasons = ", ".join(f"{key}={value}" for key, value in summary["reason_counts"].items()) or "-"
        lines.append(f"{name} | {summary['admitted']} | {summary['blocked']} | {reasons}")
    lines.extend(["", "reason deltas vs baseline"])
    for name, deltas in report["reason_deltas"].items():
        delta_text = ", ".join(f"{key}={value:+d}" for key, value in deltas.items()) or "-"
        lines.append(f"{name}: {delta_text}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, action="append", dest="paths")
    parser.add_argument("--since", help="YYYY-MM-DD filter on record ts")
    parser.add_argument("--limit", type=int, default=6, help="Replay the most recent N BLEND_DECISION candidates")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = analyze(paths=args.paths, since=args.since, limit=args.limit)
    if args.json:
        print(json.dumps(report, separators=(",", ":"), default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
