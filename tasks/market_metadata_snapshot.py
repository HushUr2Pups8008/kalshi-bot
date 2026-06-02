#!/usr/bin/env python3
"""Write a durable shadow-only market/series metadata snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kalshi.rest_client import KalshiRestClient
from kalshi.series_metadata import normalize_series_list
from utils.output_paths import DERIVED_STATE_DIR


DEFAULT_SNAPSHOT_PATH = DERIVED_STATE_DIR / "market_metadata_snapshot.json"


def _json_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_snapshot(client: KalshiRestClient) -> dict[str, Any]:
    series_payloads = client.get_all_series()
    series = normalize_series_list(series_payloads)
    payload = {
        "schema_version": 1,
        "shadow_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "series": {
            ticker: {
                "series_ticker": meta.series_ticker,
                "title": meta.title,
                "category": meta.category,
                "tags": list(meta.tags),
                "settlement_sources": [
                    {"label": source.label, "url": source.url, "domain": source.domain}
                    for source in meta.settlement_sources
                ],
                "contract_terms_url": meta.contract_terms_url,
                "rules_primary": meta.rules_primary,
                "rules_secondary": meta.rules_secondary,
                "fee_multiplier": meta.fee_multiplier,
                "fee_type": meta.fee_type,
                "can_close_early": meta.can_close_early,
            }
            for ticker, meta in sorted(series.items())
        },
    }
    payload["payload_hash"] = _json_hash(payload["series"])
    return payload


def write_snapshot(path: Path = DEFAULT_SNAPSHOT_PATH, client: KalshiRestClient | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_snapshot(client or KalshiRestClient())
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    args = parser.parse_args(argv)
    print(write_snapshot(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
