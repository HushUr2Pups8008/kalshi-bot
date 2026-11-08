"""Reviewed series settlement registry. Paper-only. Not an order path.

Estimator eligibility reads promoted rows from this registry. A local-model
proposal, a keyword settlement, and a shadow classification cannot admit a
candidate. Missing, stale, conflicting, or ambiguous data stays ineligible.

The mechanism names are operational:

- event_occurrence_count: sum discrete actions or events, such as posts,
  official actions, or qualifying mentions. A numerical result is not this
  mechanism by itself.
- published_value_lookup: read one named published observation or report
  value, such as an AAA gas price, a BLS CPI print, a weather-station
  observation, or a PortWatch transit total. Published rain accumulation and
  temperature observations are this mechanism.
- market_price: read a market or exchange price, including a settlement
  price or a candlestick close.
- opinion: read a poll, survey, or approval opinion.
- other: the rules name a mechanic outside those four.
- ambiguous: more than one mechanic fits, or the sources conflict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

MECHANISMS = (
    "event_occurrence_count",
    "published_value_lookup",
    "market_price",
    "opinion",
    "other",
    "ambiguous",
)
ESTIMABLE_MECHANISMS = (
    "event_occurrence_count",
    "published_value_lookup",
    "market_price",
    "opinion",
)
DOMAINS = ("sports", "politics", "macro", "weather", "other", "ambiguous")
STATUSES = ("promoted", "unpromoted")
_SCHEMA = "series_settlement_registry_v1"
_PATH = Path(__file__).with_name("series_settlement_registry.json")
_REQUIRED = (
    "domain",
    "settlement_mechanism",
    "source_labels",
    "source_urls",
    "fetch_hints",
    "notes",
    "status",
    "reviewed_on",
    "rule_source_version",
    "stale",
    "conflict",
)


def _norm(label: object) -> str:
    return " ".join(str(label or "").lower().split())


def sources_overlap(registry_labels: object, market_labels: object) -> bool:
    """True when one full registry label equals one full market label.

    Comparison is case-folded with collapsed whitespace. A fragment is not
    a match: ``price`` does not name ``ICE``, and ``Company`` does not name
    ``The Weather Company``. A missing label fails closed.
    """
    reg = {_norm(label) for label in _labels(registry_labels)}
    market = {_norm(label) for label in _labels(market_labels)}
    reg.discard("")
    market.discard("")
    if not reg or not market:
        return False
    return bool(reg & market)


def _labels(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple)):
        return tuple(str(label) for label in raw)
    return ()


def _validate_row(series: str, row: Mapping[str, Any]) -> None:
    if not series or any(not isinstance(row.get(key), (str, bool, list)) for key in _REQUIRED):
        raise ValueError("registry row is incomplete")
    if set(_REQUIRED) - set(row):
        raise ValueError("registry row is incomplete")
    if row["domain"] not in DOMAINS or row["settlement_mechanism"] not in MECHANISMS:
        raise ValueError("registry row is outside the schema")
    if row["status"] not in STATUSES:
        raise ValueError("registry status is outside the schema")
    if not isinstance(row["source_labels"], list) or not isinstance(row["source_urls"], list):
        raise ValueError("registry sources must be lists")
    if row["status"] == "promoted" and row["settlement_mechanism"] == "ambiguous":
        raise ValueError("an ambiguous mechanism cannot be promoted")
    if row["status"] == "promoted" and (row["stale"] or row["conflict"]):
        raise ValueError("stale or conflicting data cannot be promoted")
    if row["status"] == "promoted" and not str(row["reviewed_on"]).strip():
        raise ValueError("a promoted row needs a review date")
    if row["status"] == "promoted" and not row["source_labels"]:
        raise ValueError("a promoted row needs a source label")


def load_registry(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the checked-in registry. An invalid file raises."""
    payload = json.loads((path or _PATH).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA:
        raise ValueError("registry schema")
    rows = payload.get("rows")
    if not isinstance(rows, dict) or not rows:
        raise ValueError("registry rows")
    for series, row in rows.items():
        if not isinstance(series, str) or not isinstance(row, dict):
            raise ValueError("registry row must be an object")
        _validate_row(series, row)
    return payload


def registry_rows(path: Path | None = None) -> dict[str, dict[str, Any]]:
    rows = load_registry(path)["rows"]
    return {str(series): dict(row) for series, row in rows.items()}


def disposition(
    series_ticker: str,
    market_sources: object = (),
    *,
    registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """One shortlist row: hit, miss, unpromoted, or ambiguous.

    Ambiguous here includes a promoted row whose market sources are missing,
    stale, or in conflict with the reviewed labels. That row is not eligible.
    """
    rows = registry if registry is not None else registry_rows()
    row = rows.get(str(series_ticker or "").strip())
    if row is None:
        return "registry_miss"
    mechanism = str(row.get("settlement_mechanism") or "")
    if row.get("stale") or row.get("conflict") or mechanism not in MECHANISMS or mechanism == "ambiguous":
        return "ambiguous"
    if row.get("status") != "promoted":
        return "unpromoted"
    if not str(row.get("reviewed_on") or "").strip():
        return "ambiguous"
    if not sources_overlap(row.get("source_labels"), market_sources):
        return "ambiguous"
    return "registry_hit"


def estimator_eligible(
    series_ticker: str,
    *,
    market_sources: object = (),
    registry: Mapping[str, Mapping[str, Any]] | None = None,
    keyword_settlement: object = None,
    model_settlement: object = None,
    proposal: object = None,
) -> bool:
    """True only for a promoted, fresh, non-ambiguous estimable mechanism.

    ``keyword_settlement``, ``model_settlement``, and ``proposal`` are
    ignored. They cannot admit a candidate, and this function does not read
    a proposal file.
    """
    del keyword_settlement, model_settlement, proposal
    rows = registry if registry is not None else registry_rows()
    kind = disposition(series_ticker, market_sources, registry=rows)
    if kind != "registry_hit":
        return False
    mechanism = str(rows[str(series_ticker).strip()]["settlement_mechanism"])
    return mechanism in ESTIMABLE_MECHANISMS


def coverage(
    rows: list[Mapping[str, Any]],
    *,
    registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, int]:
    """Counts for a non-sports shortlist. Keyword and model fields are ignored."""
    book = registry if registry is not None else registry_rows()
    counts = {
        "shortlist": 0,
        "registry_hit": 0,
        "registry_miss": 0,
        "unpromoted": 0,
        "ambiguous": 0,
        "estimator_eligible": 0,
    }
    for row in rows:
        counts["shortlist"] += 1
        series = str(row.get("series_ticker") or "")
        sources = row.get("settlement_sources") or ()
        kind = disposition(series, sources, registry=book)
        counts[kind] += 1
        if estimator_eligible(series, market_sources=sources, registry=book):
            counts["estimator_eligible"] += 1
    return counts
