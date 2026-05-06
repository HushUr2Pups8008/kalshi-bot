#!/usr/bin/env python3
"""Check evidence-store ingestion freshness for replay validity."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def check_freshness(db_path: Path, *, max_age_hours: float, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "evidence" not in tables:
            return {"ok": False, "reason": "missing evidence table", "db_path": str(db_path)}
        row = conn.execute("SELECT max(ingested_ts) AS last_ingested_ts, count(*) AS evidence_rows FROM evidence").fetchone()
    finally:
        conn.close()
    last = row["last_ingested_ts"] if row else None
    if not last:
        return {"ok": False, "reason": "no evidence rows", "db_path": str(db_path)}
    last_dt = _parse_ts(str(last))
    age_hours = (now - last_dt).total_seconds() / 3600.0
    return {
        "ok": age_hours <= max_age_hours,
        "db_path": str(db_path),
        "last_ingested_ts": last_dt.isoformat(),
        "age_hours": round(age_hours, 3),
        "max_age_hours": max_age_hours,
        "evidence_rows": int(row["evidence_rows"]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/evidence_store.db"))
    parser.add_argument("--max-age-hours", type=float, default=6.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = check_freshness(args.db, max_age_hours=args.max_age_hours)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"ok={result['ok']} last_ingested_ts={result.get('last_ingested_ts')} age_hours={result.get('age_hours')}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
