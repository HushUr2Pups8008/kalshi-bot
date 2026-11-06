#!/usr/bin/env python3
"""Paper-only local triage. On demand, or the same job with --nightly.

This job is not installed in launchd. It reads public market and series
pages through the existing discovery pager, writes shadow files, and may
ask a local model to classify rules. It does not place an order, admit a
live candidate, edit the live series file, or call xAI.

Missing order-book depth stays missing. The deterministic class is
provisional. A disagreement with the local model stays ambiguous.
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
    DiscoveryFilters,
    _source_labels,
    apply_deterministic_class,
    count_reasons,
    keep_best_per_event,
    public_row,
    review_class,
    screen_market,
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


def _enrich_rules(row: dict[str, Any], series: Any, detail_fetcher: Callable[[str], Any] | None) -> None:
    if series is not None and not row["category"]:
        row["category"] = str(getattr(series, "category", "") or "")
    if series is not None and not str(row["rules_primary"]).strip():
        row["rules_primary"] = str(getattr(series, "rules_primary", "") or "")
        if not str(row["rules_secondary"]).strip():
            row["rules_secondary"] = str(getattr(series, "rules_secondary", "") or "")
        if not row["settlement_sources"]:
            row["settlement_sources"] = [
                source.label
                for source in (getattr(series, "settlement_sources", ()) or ())
                if getattr(source, "label", "")
            ]
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
    """Rules beside both classes. human_class stays empty for a person to fill.

    The review class is already ambiguous when the two labels disagree.
    This sample does not choose the model.
    """
    sample: list[dict[str, Any]] = []
    kept = [row for row in rows if not row.get("rejection_reason")]
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
                "deterministic_class": row.get("deterministic_class") or "",
                "deterministic_ambiguity": row.get("deterministic_ambiguity") or "",
                "local_class": row.get("local_class") or "",
                "local_outcome": row.get("local_outcome") or "",
                "local_ambiguity": row.get("local_ambiguity") or "",
                "review_class": row.get("review_class") or "",
                "human_class": None,
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
    return {
        "schema": "local_triage_funnel_v1",
        "ts": ts,
        "mode": mode,
        "provisional": True,
        "model_id": classifier.model_id,
        "runtime": classifier.runtime,
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
        "shortlist": len(shortlist),
        "not_best_in_event": reasons.get("not_best_in_event", 0),
        "deterministic_classes": _histogram(rows, "deterministic_class"),
        "local_classes": dict(sorted(Counter(str(row.get("class") or "") for row in ok if row.get("class")).items())),
        "local_outcomes": dict(sorted(outcomes.items())),
        "review_classes": _histogram(rows, "review_class"),
        "classified_ok": len(ok),
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
    for row in rows:
        if row.get("rejection_reason"):
            continue
        series = series_by_ticker.get(str(row.get("series_ticker") or ""))
        _enrich_rules(row, series, detail_for)
        status_name = _attach_depth(row, book_for)
        depth_status[str(row.get("ticker") or "")] = status_name
    apply_deterministic_class(rows, sports_prefixes=sports_prefixes)
    shortlist_n = sum(1 for row in rows if not row.get("rejection_reason"))
    print(f"shortlist_ready={shortlist_n}", flush=True)
    for row in rows:
        if row.get("rejection_reason"):
            row["local_class"] = ""
            row["local_outcome"] = ""
            row["local_ambiguity"] = ""
            row["review_class"] = ""
            continue
        record = classifier.classify(row)
        record["deterministic_class"] = row.get("deterministic_class") or ""
        record["review_class"] = review_class(
            str(row.get("deterministic_class") or ""),
            str(record.get("outcome") or ""),
            str(record.get("class") or ""),
        )
        row["local_class"] = record["class"] if record["outcome"] == "ok" else ""
        row["local_outcome"] = record["outcome"]
        row["local_ambiguity"] = record["ambiguity"] if record["outcome"] == "ok" else ""
        row["review_class"] = record["review_class"]
        classifications.append(record)
        if len(classifications) == 1 or len(classifications) % 10 == 0:
            print(
                f"classified={len(classifications)} outcome={record['outcome']}",
                flush=True,
            )
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
    }
    _write_jsonl(paths["discovery"], discovery_rows)
    _write_jsonl(paths["classification"], classifications)
    _write_json(paths["series_cache"], [_cache_row(meta) for meta in series_by_ticker.values()])
    _write_json(paths["funnel"], funnel)
    _write_json(
        paths["review"],
        {
            "ts": ts,
            "note": (
                "Disagreement stays ambiguous. The local class is not selected "
                "over the deterministic class. human_class is for a person."
            ),
            "rows": review_rows,
        },
    )
    funnel["paths"] = {name: str(path) for name, path in paths.items()}
    return funnel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper-only local triage shadow job")
    parser.add_argument("--nightly", action="store_true", help="Same job. Not a launchd install.")
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT))
    args = parser.parse_args(argv)
    from kalshi.rest_client import KalshiRestClient

    funnel = run_shadow(
        KalshiRestClient(),
        now=datetime.now(timezone.utc),
        out_dir=Path(args.out_dir),
        filters=DiscoveryFilters.from_env(),
        classifier=LocalClassifier.from_env(),
        sports_prefixes=sports_prefixes(),
        mode="nightly" if args.nightly else "on_demand",
    )
    print(f"model={funnel['model_id'] or 'unconfigured'}", flush=True)
    print(f"runtime={funnel['runtime']}", flush=True)
    print(f"shortlist={funnel['shortlist']}", flush=True)
    print(f"funnel={funnel['paths']['funnel']}", flush=True)
    print(f"discovery={funnel['paths']['discovery']}", flush=True)
    print(f"classification={funnel['paths']['classification']}", flush=True)
    print(f"review={funnel['paths']['review']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
