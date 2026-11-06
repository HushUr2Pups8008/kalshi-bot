#!/usr/bin/env python3
"""Paper-only local triage. On demand, or the same job with --nightly.

This job is not installed in launchd. It reads public market and series
pages through the existing discovery pager, writes shadow files, and may
ask a local model to classify rules. It does not place an order, admit a
live candidate, edit the live series file, or call xAI.

Missing order-book depth stays missing. Domain and settlement are separate
provisional axes. A disagreement makes only that axis ambiguous. Sports-domain
rows are not sent to the local model. Local output is not an order.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import MARKET_SERIES_BLOCKLIST_PREFIXES
from kalshi.series_metadata import normalize_series_payload
from scripts.universe_discovery import _page
from utils.local_triage import (
    DOMAINS,
    SETTLEMENTS,
    DiscoveryFilters,
    _source_labels,
    apply_deterministic_axes,
    count_reasons,
    det_match_rates,
    human_axis_scores,
    keep_best_per_event,
    pilot_composition,
    public_row,
    review_axis,
    screen_market,
    select_pilot,
    touch_depth,
)
from utils.local_triage_classifier import LocalClassifier

_NOT_SPORTS = frozenset({"KXAPRPOTUS", "KXPOLLPOTUS", "KXCBDECISION"})
_FORBIDDEN_OUTPUT_NAMES = frozenset(
    {
        "grok_veto_decisions.jsonl",
        "grok_edge_calls.jsonl",
        "paper_trades.db",
        "live-fill-log.md",
        "politics_loss_limit_halt.json",
    }
)
_DEFAULT_OUT = Path("logs/state/local_triage")


def sports_prefixes() -> tuple[str, ...]:
    """Blocklist prefixes that are sports. Poll and central-bank names stay out.

    The class that uses this list is provisional. It is not a live-series edit.
    """
    return tuple(prefix for prefix in MARKET_SERIES_BLOCKLIST_PREFIXES if prefix not in _NOT_SPORTS)


def assert_shadow_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.name in _FORBIDDEN_OUTPUT_NAMES:
        raise RuntimeError("local triage does not write live trading state")
    return resolved


def _series_map(rows: list[Any]) -> tuple[dict[str, Any], int]:
    found: dict[str, Any] = {}
    skipped = 0
    for item in rows:
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            meta = normalize_series_payload(item)
        except Exception:
            skipped += 1
            continue
        if not meta.series_ticker:
            skipped += 1
            continue
        found[meta.series_ticker] = meta
    return found, skipped


def _detail_text(detail: Any) -> tuple[str, str, tuple[str, ...]]:
    if detail is None:
        return "", "", ()
    if isinstance(detail, Mapping):
        return (
            str(detail.get("rules_primary") or ""),
            str(detail.get("rules_secondary") or ""),
            _source_labels(detail.get("settlement_sources") or detail.get("settlement_source_urls")),
        )
    return (
        str(getattr(detail, "rules_primary", "") or ""),
        str(getattr(detail, "rules_secondary", "") or ""),
        _source_labels(getattr(detail, "settlement_sources", ()) or ()),
    )


def _series_source_labels(series: Any) -> list[str]:
    labels: list[str] = []
    for source in getattr(series, "settlement_sources", ()) or ():
        label = str(getattr(source, "label", "") or "").strip()
        if label:
            labels.append(label)
    return labels


def _enrich_rules(row: dict[str, Any], series: Any, detail_fetcher: Callable[[str], Any] | None) -> None:
    if series is not None and not row["category"]:
        row["category"] = str(getattr(series, "category", "") or "")
    if series is not None and not str(row["rules_primary"]).strip():
        row["rules_primary"] = str(getattr(series, "rules_primary", "") or "")
        if not str(row["rules_secondary"]).strip():
            row["rules_secondary"] = str(getattr(series, "rules_secondary", "") or "")
    # Market rules do not carry the series source list. Copy it whenever the row has none.
    if series is not None and not row["settlement_sources"]:
        row["settlement_sources"] = _series_source_labels(series)
    if not str(row["rules_primary"]).strip() and detail_fetcher is not None:
        try:
            detail = detail_fetcher(row["ticker"])
        except Exception:
            detail = None
        rules, secondary, labels = _detail_text(detail)
        if rules:
            row["rules_primary"] = rules
        if secondary and not str(row["rules_secondary"]).strip():
            row["rules_secondary"] = secondary
        if labels and not row["settlement_sources"]:
            row["settlement_sources"] = list(labels)


def _attach_depth(row: dict[str, Any], fetcher: Callable[[str], Any] | None) -> str:
    if fetcher is None:
        return "not_fetched"
    try:
        book = fetcher(row["ticker"])
    except Exception:
        return "fetch_failed"
    if book is None:
        return "missing"
    row.update(touch_depth(book))
    if any(row.get(key) is None for key in ("yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size")):
        return "partial"
    return "ok"


def _cache_row(meta: Any) -> dict[str, Any]:
    return {
        "series_ticker": meta.series_ticker,
        "title": meta.title,
        "category": meta.category,
        "tags": list(meta.tags),
        "rules_primary": meta.rules_primary,
        "rules_secondary": meta.rules_secondary,
        "settlement_sources": [
            {"label": source.label, "url": source.url, "domain": source.domain}
            for source in meta.settlement_sources
        ],
        "raw_payload_hash": meta.raw_payload_hash,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    lines = [json.dumps(row, ensure_ascii=True, sort_keys=True) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build_review_sample(rows: list[Mapping[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    """Rules beside both axes. Human fields stay empty for a person to fill.

    A disagreement is already ambiguous on that axis only.
    This sample does not choose the model.
    """
    sample: list[dict[str, Any]] = []
    kept = [row for row in rows if row.get("track") == "main"]
    kept.sort(key=lambda row: str(row.get("ticker") or ""))
    for row in kept[:limit]:
        sample.append(
            {
                "ticker": row.get("ticker") or "",
                "series_ticker": row.get("series_ticker") or "",
                "title": row.get("title") or "",
                "rules_primary": row.get("rules_primary") or "",
                "settlement_sources": list(row.get("settlement_sources") or ()),
                "rule_version": row.get("rule_version") or "missing",
                "deterministic_domain": row.get("deterministic_domain") or "",
                "deterministic_settlement": row.get("deterministic_settlement") or "",
                "deterministic_ambiguity": row.get("deterministic_ambiguity") or "",
                "local_domain": row.get("local_domain") or "",
                "local_settlement": row.get("local_settlement") or "",
                "local_outcome": row.get("local_outcome") or "",
                "local_ambiguity": row.get("local_ambiguity") or "",
                "review_domain": row.get("review_domain") or "",
                "review_settlement": row.get("review_settlement") or "",
                "human_domain": None,
                "human_settlement": None,
            }
        )
    return sample


def _histogram(rows: list[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("rejection_reason"):
            continue
        label = str(row.get(key) or "")
        if label:
            counts[label] += 1
    return dict(sorted(counts.items()))


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return int(round(ordered[low] * (1 - weight) + ordered[high] * weight))


def _latency(records: list[Mapping[str, Any]]) -> dict[str, int | None]:
    ok = [int(row.get("latency_ms") or 0) for row in records if row.get("outcome") == "ok"]
    failed = [int(row.get("latency_ms") or 0) for row in records if row.get("outcome") != "ok"]
    return {
        "ok_p50_ms": _percentile(ok, 0.50),
        "ok_p90_ms": _percentile(ok, 0.90),
        "ok_max_ms": max(ok) if ok else None,
        "other_p50_ms": _percentile(failed, 0.50),
        "other_p90_ms": _percentile(failed, 0.90),
        "other_max_ms": max(failed) if failed else None,
    }


def schema_rates(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Valid rate after the retry. Timeouts stay timeouts and are not relabeled.

    The schema gate uses responses that returned. A timeout is reported beside
    that rate and is not counted as a valid body or as a malformed body.
    """
    attempted = len(records)
    ok = sum(1 for row in records if row.get("outcome") == "ok")
    malformed = sum(1 for row in records if row.get("outcome") == "malformed")
    timeout = sum(1 for row in records if row.get("outcome") == "timeout")
    completed = attempted - timeout
    schema_valid_rate = (ok / completed) if completed else None
    return {
        "attempted": attempted,
        "ok": ok,
        "malformed": malformed,
        "timeout": timeout,
        "valid_rate": (ok / attempted) if attempted else None,
        "timeout_rate": (timeout / attempted) if attempted else None,
        "schema_valid_rate": schema_valid_rate,
        "gate": 0.99,
        "gate_met": bool(completed) and schema_valid_rate is not None and schema_valid_rate >= 0.99,
        "gate_definition": "ok / (attempted - timeout) >= 0.99; timeouts are separate",
    }


_LABELS_PATH = _REPO_ROOT / "tests" / "fixtures" / "local_triage_human_labels.json"


def load_human_labels(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or _LABELS_PATH
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("human labels must be a list")
    checked: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("human label row must be an object")
        if row.get("human_domain") not in DOMAINS or row.get("human_settlement") not in SETTLEMENTS:
            raise ValueError("human label axes are outside the schema")
        checked.append(row)
    return checked


def _model_report(
    records: list[Mapping[str, Any]],
    *,
    labels: list[Mapping[str, Any]],
    probe: Mapping[str, Any] | None,
) -> dict[str, Any]:
    rates = schema_rates(records)
    return {
        "model_id": records[0].get("model_id") if records else "",
        "runtime": records[0].get("runtime") if records else "",
        "schema_mode": records[0].get("schema_mode") if records else "",
        "schema_enforced": bool(records and records[0].get("schema_enforced")),
        "probe": probe,
        "rates": rates,
        "latency_ms": _latency(records),
        "det_match": det_match_rates(list(records)),
        "human": human_axis_scores(list(records), labels),
        "retry_outcomes": dict(
            sorted(Counter(str(row.get("retry_outcome") or "") for row in records).items())
        ),
        "parser_failures": dict(
            sorted(
                Counter(
                    str(row.get("parser_failure") or "") for row in records if row.get("parser_failure")
                ).items()
            )
        ),
    }


def build_funnel(
    *,
    ts: str,
    mode: str,
    market_pages: int,
    series_pages: int,
    series_skipped: int,
    rows: list[Mapping[str, Any]],
    classifications: list[Mapping[str, Any]],
    filters: DiscoveryFilters,
    classifier: LocalClassifier,
    elapsed_s: float,
    depth_status: Mapping[str, str],
) -> dict[str, Any]:
    reasons = count_reasons(rows)
    shortlist = [row for row in rows if not row.get("rejection_reason")]
    outcomes: Counter[str] = Counter(str(row.get("outcome") or "") for row in classifications)
    ok = [row for row in classifications if row.get("outcome") == "ok"]
    ambiguous_ok = sum(1 for row in ok if row.get("ambiguity") != "none")
    attempted = len(classifications)
    missing_depth = sum(
        1
        for row in shortlist
        if any(row.get(key) is None for key in ("yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size"))
    )
    rates = schema_rates(list(classifications))
    before_sports = len(shortlist)
    sports_excluded = sum(1 for row in rows if row.get("track") == "sports_shadow")
    main_rows = [row for row in rows if row.get("track") == "main"]
    pilot_rows = [row for row in main_rows if row.get("local_outcome") not in ("", "not_in_pilot")]
    return {
        "schema": "local_triage_funnel_v2",
        "ts": ts,
        "mode": mode,
        "provisional": True,
        "model_id": classifier.model_id,
        "runtime": classifier.runtime,
        "schema_mode": classifier.schema_mode,
        "schema_enforced": classifier.schema_enforced,
        "filters": {
            "min_volume_24h": format(filters.min_volume_24h, "f"),
            "min_open_interest": format(filters.min_open_interest, "f"),
            "max_spread_cents": filters.max_spread_cents,
            "min_close_hours": filters.min_close_hours,
            "max_close_days": filters.max_close_days,
            "min_yes_ask_cents": filters.min_yes_ask_cents,
            "max_yes_ask_cents": filters.max_yes_ask_cents,
        },
        "market_pages": market_pages,
        "series_pages": series_pages,
        "series_skipped": series_skipped,
        "raw_markets": len(rows),
        "rejected": reasons,
        "passed_filters": sum(1 for row in rows if row.get("rejection_reason") in ("", "not_best_in_event")),
        "shortlist_before_sports": before_sports,
        "sports_excluded": sports_excluded,
        "shortlist": len(main_rows),
        "pilot": len(pilot_rows),
        "pilot_composition": pilot_composition(pilot_rows),
        "not_best_in_event": reasons.get("not_best_in_event", 0),
        "deterministic_domains": _histogram(rows, "deterministic_domain"),
        "deterministic_settlements": _histogram(rows, "deterministic_settlement"),
        "local_domains": dict(sorted(Counter(str(row.get("domain") or "") for row in ok if row.get("domain")).items())),
        "local_settlements": dict(
            sorted(Counter(str(row.get("settlement") or "") for row in ok if row.get("settlement")).items())
        ),
        "local_outcomes": dict(sorted(outcomes.items())),
        "review_domains": _histogram(rows, "review_domain"),
        "review_settlements": _histogram(rows, "review_settlement"),
        "classified_ok": len(ok),
        "schema_valid_rate": rates["schema_valid_rate"],
        "valid_rate": rates["valid_rate"],
        "timeout_rate": rates["timeout_rate"],
        "gate_met": rates["gate_met"],
        "det_match": det_match_rates(list(classifications)),
        "latency_ms": _latency(list(classifications)),
        "ambiguity_ok": ambiguous_ok,
        "ambiguity_rate": (ambiguous_ok / len(ok)) if ok else None,
        "malformed": outcomes.get("malformed", 0),
        "malformed_rate": (outcomes.get("malformed", 0) / attempted) if attempted else None,
        "attempted": attempted,
        "missing": {
            "volume": reasons.get("volume_missing", 0),
            "open_interest": reasons.get("open_interest_missing", 0),
            "close": reasons.get("close_missing", 0),
            "quote": reasons.get("missing_quote", 0),
            "rules": sum(1 for row in shortlist if row.get("rule_version") == "missing"),
            "depth": missing_depth,
        },
        "depth_status": dict(sorted(Counter(depth_status.values()).items())),
        "runtime_seconds": round(elapsed_s, 6),
    }


def run_shadow(
    client: Any,
    *,
    now: datetime,
    out_dir: Path,
    filters: DiscoveryFilters,
    classifier: LocalClassifier,
    sports_prefixes: tuple[str, ...],
    orderbook_for: Callable[[str], Any] | None = None,
    market_for: Callable[[str], Any] | None = None,
    mode: str = "on_demand",
    max_classify: int | None = None,
    send_to_model: bool = True,
    probe: bool = False,
    compare_model: str = "",
    labels_path: Path | None = None,
) -> dict[str, Any]:
    """Page the public books, screen them, and write shadow evidence only."""
    destination = assert_shadow_dir(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    clock = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    ts = clock.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = clock.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    market_pages, markets = _page(client, "/markets", "markets", 1000)
    series_pages, series_rows = _page(client, "/series", "series", 200)
    series_by_ticker, series_skipped = _series_map(series_rows)
    rows: list[dict[str, Any]] = []
    for market in markets:
        if not isinstance(market, dict):
            rows.append(
                public_row(
                    {
                        "ticker": "",
                        "rejection_reason": "not_active",
                        "provisional": True,
                        **{
                            "yes_bid_size": None,
                            "yes_ask_size": None,
                            "no_bid_size": None,
                            "no_ask_size": None,
                        },
                    },
                    ts=ts,
                )
            )
            continue
        rows.append(screen_market(market, filters, now=clock))
    keep_best_per_event(rows)
    book_for = orderbook_for
    if book_for is None and hasattr(client, "get_market_orderbook"):
        def book_for(ticker: str, _client: Any = client) -> Any:
            return _client.get_market_orderbook(ticker, depth=1)
    detail_for = market_for
    if detail_for is None and hasattr(client, "get_market"):
        def detail_for(ticker: str, _client: Any = client) -> Any:
            return _client.get_market(ticker)
    depth_status: dict[str, str] = {}
    classifications: list[dict[str, Any]] = []
    depth_n = 0
    for row in rows:
        if row.get("rejection_reason"):
            continue
        series = series_by_ticker.get(str(row.get("series_ticker") or ""))
        _enrich_rules(row, series, detail_for)
        status_name = _attach_depth(row, book_for)
        depth_status[str(row.get("ticker") or "")] = status_name
        depth_n += 1
        if depth_n == 1 or depth_n % 50 == 0:
            print(f"depth={depth_n}", flush=True)
    apply_deterministic_axes(rows, sports_prefixes=sports_prefixes)
    main_rows: list[dict[str, Any]] = []
    sports_rows: list[dict[str, Any]] = []
    for row in rows:
        if row.get("rejection_reason"):
            row["track"] = ""
            _clear_local(row)
            continue
        if row.get("deterministic_domain") == "sports":
            row["track"] = "sports_shadow"
            _clear_local(row)
            row["local_outcome"] = "sports_excluded"
            sports_rows.append(row)
            continue
        row["track"] = "main"
        _clear_local(row)
        main_rows.append(row)
    if max_classify is None:
        pilot = list(main_rows)
    else:
        pilot = select_pilot(main_rows, max_classify)
    pilot_ids = {str(row.get("ticker") or "") for row in pilot}
    for row in main_rows:
        if str(row.get("ticker") or "") in pilot_ids:
            row["local_outcome"] = "pilot_held" if not send_to_model else ""
        else:
            row["local_outcome"] = "not_in_pilot"
    print(
        f"shortlist={len(main_rows)} sports_excluded={len(sports_rows)} pilot={len(pilot)}",
        flush=True,
    )
    labels = load_human_labels(labels_path)
    probe_report = _maybe_probe(classifier, probe=probe and send_to_model and bool(pilot))
    compare_probe = None
    compare_records: list[dict[str, Any]] = []
    compare_client = None
    if send_to_model and compare_model.strip() and pilot:
        compare_client = LocalClassifier(
            base_url=classifier.base_url,
            model_id=compare_model.strip(),
            runtime=classifier.runtime,
            timeout_s=classifier.timeout_s,
            opener=classifier._opener,
            clock=classifier._clock,
        )
        compare_probe = _maybe_probe(compare_client, probe=probe)
    if send_to_model:
        for row in pilot:
            record = _classify_row(classifier, row)
            classifications.append(record)
            if len(classifications) == 1 or len(classifications) % 10 == 0:
                print(
                    f"classified={len(classifications)} outcome={record['outcome']} "
                    f"mode={record['schema_mode']} http={record['http_status']} "
                    f"failure={record['parser_failure']} retry={record['retry_outcome']}",
                    flush=True,
                )
        if compare_client is not None:
            for row in pilot:
                compare_records.append(_classify_row(compare_client, row, mutate=False))
    discovery_rows = [public_row(row, ts=ts) for row in rows]
    review_rows = build_review_sample(rows)
    funnel = build_funnel(
        ts=ts,
        mode=mode,
        market_pages=market_pages,
        series_pages=series_pages,
        series_skipped=series_skipped,
        rows=rows,
        classifications=classifications,
        filters=filters,
        classifier=classifier,
        elapsed_s=time.perf_counter() - started,
        depth_status=depth_status,
    )
    paths = {
        "discovery": destination / f"discovery-{stamp}.jsonl",
        "classification": destination / f"classification-{stamp}.jsonl",
        "series_cache": destination / f"series-cache-{stamp}.json",
        "funnel": destination / f"funnel-{stamp}.json",
        "review": destination / f"review-{stamp}.json",
        "sports": destination / f"sports-shadow-{stamp}.jsonl",
        "pilot": destination / f"pilot-{stamp}.json",
        "evaluation": destination / f"evaluation-{stamp}.json",
    }
    if compare_records:
        slug = _model_slug(compare_model)
        paths["classification_compare"] = destination / f"classification-{stamp}-{slug}.jsonl"
    pilot_payload = [public_row(row, ts=ts) for row in pilot]
    evaluation = {
        "ts": ts,
        "gate": 0.99,
        "det_match_rate_meaning": "agreement_not_accuracy",
        "prompt_is_not_schema_enforcement": True,
        "models": [
            _model_report(classifications, labels=labels, probe=probe_report),
        ],
    }
    if compare_records:
        evaluation["models"].append(_model_report(compare_records, labels=labels, probe=compare_probe))
    _write_jsonl(paths["discovery"], discovery_rows)
    _write_jsonl(paths["classification"], classifications)
    _write_jsonl(paths["sports"], [public_row(row, ts=ts) for row in sports_rows])
    _write_json(paths["series_cache"], [_cache_row(meta) for meta in series_by_ticker.values()])
    _write_json(paths["funnel"], funnel)
    _write_json(
        paths["pilot"],
        {
            "ts": ts,
            "sports_excluded": len(sports_rows),
            "shortlist": len(main_rows),
            "composition": pilot_composition(pilot),
            "rows": pilot_payload,
        },
    )
    _write_json(paths["evaluation"], evaluation)
    _write_json(
        paths["review"],
        {
            "ts": ts,
            "note": (
                "Disagreement makes only that axis ambiguous. "
                "human_domain and human_settlement are for a person. "
                "det_match_rate is agreement, not accuracy."
            ),
            "rows": review_rows,
        },
    )
    if compare_records:
        _write_jsonl(paths["classification_compare"], compare_records)
    funnel["paths"] = {name: str(path) for name, path in paths.items()}
    funnel["schema_probe"] = probe_report
    return funnel


def classify_pilot_file(
    pilot_path: Path,
    *,
    out_dir: Path,
    classifier: LocalClassifier,
    now: datetime,
    probe: bool = False,
    compare_model: str = "",
    labels_path: Path | None = None,
) -> dict[str, Any]:
    """Classify a saved pilot. This does not page markets and does not discover again."""
    destination = assert_shadow_dir(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("pilot file has no rows")
    clock = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    stamp = clock.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    labels = load_human_labels(labels_path)
    probe_report = _maybe_probe(classifier, probe=probe and bool(rows))
    records = [_classify_row(classifier, row) for row in rows if isinstance(row, dict)]
    compare_records: list[dict[str, Any]] = []
    compare_probe = None
    if compare_model.strip() and rows:
        other = LocalClassifier(
            base_url=classifier.base_url,
            model_id=compare_model.strip(),
            runtime=classifier.runtime,
            timeout_s=classifier.timeout_s,
            opener=classifier._opener,
            clock=classifier._clock,
        )
        compare_probe = _maybe_probe(other, probe=probe)
        compare_records = [_classify_row(other, row, mutate=False) for row in rows if isinstance(row, dict)]
    evaluation = {
        "ts": clock.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_pilot": str(pilot_path),
        "gate": 0.99,
        "det_match_rate_meaning": "agreement_not_accuracy",
        "prompt_is_not_schema_enforcement": True,
        "models": [_model_report(records, labels=labels, probe=probe_report)],
    }
    if compare_records:
        evaluation["models"].append(_model_report(compare_records, labels=labels, probe=compare_probe))
    paths = {
        "classification": destination / f"classification-{stamp}.jsonl",
        "evaluation": destination / f"evaluation-{stamp}.json",
    }
    if compare_records:
        paths["classification_compare"] = destination / f"classification-{stamp}-{_model_slug(compare_model)}.jsonl"
        _write_jsonl(paths["classification_compare"], compare_records)
    _write_jsonl(paths["classification"], records)
    _write_json(paths["evaluation"], evaluation)
    return {"paths": {name: str(path) for name, path in paths.items()}, "evaluation": evaluation}


def _clear_local(row: dict[str, Any]) -> None:
    row["local_domain"] = ""
    row["local_settlement"] = ""
    row["local_outcome"] = ""
    row["local_ambiguity"] = ""
    row["review_domain"] = ""
    row["review_settlement"] = ""


def _model_slug(model_id: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in model_id.strip())
    return cleaned.strip("-") or "model"


def _maybe_probe(classifier: LocalClassifier, *, probe: bool) -> dict[str, Any] | None:
    if not probe or classifier.refused_paid_host or not classifier.base_url or not classifier.model_id:
        return None
    report = classifier.probe()
    print(
        f"schema_probe model={report['model_id']} mode={report['selected_mode']} "
        f"enforced={report['schema_enforced']}",
        flush=True,
    )
    for attempt in report["attempts"]:
        print(
            f"schema_probe_attempt mode={attempt['mode']} http={attempt['http_status']} "
            f"constrained={attempt['constrained']} failure={attempt['parser_failure']}",
            flush=True,
        )
    return report


def _classify_row(
    classifier: LocalClassifier,
    row: Mapping[str, Any],
    *,
    mutate: bool = True,
) -> dict[str, Any]:
    record = classifier.classify(row)
    record["series_ticker"] = str(row.get("series_ticker") or "")
    record["rules_primary"] = str(row.get("rules_primary") or "")
    record["deterministic_domain"] = str(row.get("deterministic_domain") or "")
    record["deterministic_settlement"] = str(row.get("deterministic_settlement") or "")
    record["review_domain"] = review_axis(
        record["deterministic_domain"],
        str(record.get("outcome") or ""),
        str(record.get("domain") or ""),
        DOMAINS,
    )
    record["review_settlement"] = review_axis(
        record["deterministic_settlement"],
        str(record.get("outcome") or ""),
        str(record.get("settlement") or ""),
        SETTLEMENTS,
    )
    if mutate and isinstance(row, dict):
        if record["outcome"] == "ok":
            row["local_domain"] = record["domain"]
            row["local_settlement"] = record["settlement"]
            row["local_ambiguity"] = record["ambiguity"]
        row["local_outcome"] = record["outcome"]
        row["review_domain"] = record["review_domain"]
        row["review_settlement"] = record["review_settlement"]
    return record


def _print_funnel(funnel: Mapping[str, Any]) -> None:
    print(f"model={funnel.get('model_id') or 'unconfigured'}", flush=True)
    print(f"runtime={funnel.get('runtime')}", flush=True)
    print(f"sports_excluded={funnel.get('sports_excluded')}", flush=True)
    print(f"shortlist={funnel.get('shortlist')}", flush=True)
    print(f"pilot={funnel.get('pilot')}", flush=True)
    print(f"schema_enforced={funnel.get('schema_enforced')}", flush=True)
    paths = funnel.get("paths") or {}
    for name in ("funnel", "discovery", "classification", "sports", "pilot", "evaluation", "review"):
        if name in paths:
            print(f"{name}={paths[name]}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper-only local triage shadow job")
    parser.add_argument("--nightly", action="store_true", help="Same job. Not a launchd install.")
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT))
    parser.add_argument("--max-classify", type=int, default=50)
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--pilot-file", default="")
    parser.add_argument("--compare-model", default="")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    if args.discover_only and args.pilot_file:
        raise SystemExit("discover-only does not classify a pilot file")
    classifier = LocalClassifier.from_env()
    if args.pilot_file:
        result = classify_pilot_file(
            Path(args.pilot_file),
            out_dir=Path(args.out_dir),
            classifier=classifier,
            now=datetime.now(timezone.utc),
            probe=args.probe or bool(classifier.base_url and classifier.model_id),
            compare_model=args.compare_model,
        )
        print(f"evaluation={result['paths']['evaluation']}", flush=True)
        print(f"classification={result['paths']['classification']}", flush=True)
        for model in result["evaluation"]["models"]:
            rates = model["rates"]
            print(
                f"model={model['model_id']} schema_enforced={model['schema_enforced']} "
                f"schema_valid_rate={rates['schema_valid_rate']} timeout_rate={rates['timeout_rate']} "
                f"gate_met={rates['gate_met']}",
                flush=True,
            )
        return 0
    from kalshi.rest_client import KalshiRestClient

    configured = bool(classifier.base_url and classifier.model_id and not classifier.refused_paid_host)
    funnel = run_shadow(
        KalshiRestClient(),
        now=datetime.now(timezone.utc),
        out_dir=Path(args.out_dir),
        filters=DiscoveryFilters.from_env(),
        classifier=classifier,
        sports_prefixes=sports_prefixes(),
        mode="nightly" if args.nightly else "on_demand",
        max_classify=args.max_classify,
        send_to_model=not args.discover_only,
        probe=args.probe or (configured and not args.discover_only),
        compare_model=args.compare_model,
    )
    _print_funnel(funnel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
