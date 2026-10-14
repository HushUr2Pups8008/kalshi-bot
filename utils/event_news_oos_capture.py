"""Append prospective politics OOS captures.

The file name is the one named by registration ``politics-oos``.
A line written here is a capture of a new paper-shadow row. It is not
``registered_oos`` evidence, and the replay gate does not accept it.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from utils.event_news_desk_flags import event_news_series_family

POLITICS_OOS_REGISTRATION_ID = "politics-oos"
POLITICS_OOS_CORPUS_NAME = "corpus_politics_oos.jsonl"
_LOCK = threading.Lock()


def politics_oos_corpus_path(root: Path | None = None) -> Path:
    """Return the gitignored capture path the registration names."""
    base = Path(__file__).resolve().parents[1] if root is None else Path(root)
    return base / "logs" / "edge_replay" / POLITICS_OOS_CORPUS_NAME


def append_politics_oos_capture(
    *,
    trade_id: str,
    ticker: str,
    side: str,
    price_cents: int,
    contracts: int,
    root: Path | None = None,
) -> bool:
    """Append one new capture. Return False when the row is not written."""
    if not isinstance(trade_id, str) or not trade_id.strip():
        return False
    if not isinstance(ticker, str) or not ticker.strip():
        return False
    if isinstance(price_cents, bool) or not isinstance(price_cents, int):
        return False
    if isinstance(contracts, bool) or not isinstance(contracts, int) or contracts <= 0:
        return False
    row = {
        "contracts": contracts,
        "evidence_class": "prospective_capture",
        "market_family": event_news_series_family(ticker),
        "oos_registration_id": POLITICS_OOS_REGISTRATION_ID,
        "price_cents": price_cents,
        "recorded_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "side": str(side or "").strip().lower(),
        "ticker": ticker.strip(),
        "trade_id": trade_id.strip(),
    }
    if row["evidence_class"] == "registered_oos":
        return False
    line = json.dumps(
        row,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    path = politics_oos_corpus_path(root)
    with _LOCK:
        if path.exists():
            for existing in path.read_text(encoding="utf-8").splitlines():
                if not existing.strip():
                    continue
                try:
                    prior = json.loads(existing)
                except json.JSONDecodeError:
                    continue
                if isinstance(prior, dict) and prior.get("trade_id") == row["trade_id"]:
                    return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return True
