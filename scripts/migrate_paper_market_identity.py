#!/usr/bin/env python3
"""Plan and explicitly apply canonical paper-market identity migration."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kalshi.rest_client import KalshiRestClient  # noqa: E402
from polymarket.public_client import PolymarketPublicClient  # noqa: E402
from trading.venue import Venue, normalize_venue  # noqa: E402


@dataclass(frozen=True)
class IdentityLookup:
    kind: str
    canonical_id: str | None = None
    reason: str | None = None

    @classmethod
    def mapped(cls, canonical_id: str) -> "IdentityLookup":
        return cls("mapped", canonical_id=str(canonical_id))

    @classmethod
    def missing(cls) -> "IdentityLookup":
        return cls("missing", reason="not_found")

    @classmethod
    def transport(cls, reason: str = "transport_failure") -> "IdentityLookup":
        return cls("transport", reason=reason)

    @classmethod
    def ambiguous(cls) -> "IdentityLookup":
        return cls("ambiguous", reason="multiple_exact_matches")

    @classmethod
    def mismatch(cls, reason: str = "alias_mismatch") -> "IdentityLookup":
        return cls("mismatch", reason=reason)

    @classmethod
    def unsupported(cls, reason: str = "unsupported_venue") -> "IdentityLookup":
        return cls("unsupported", reason=reason)


class IdentityResolver(Protocol):
    def lookup(self, venue: Venue, alias: str) -> IdentityLookup: ...


@dataclass(frozen=True)
class IdentityRow:
    trade_id: str
    venue: str
    ticker: str
    resolved: int
    venue_market_id: str | None
    identity_status: str | None
    quarantine_reason: str | None
    target_market_id: str | None = None
    target_reason: str | None = None

    def fingerprint_values(self) -> tuple[object, ...]:
        return (
            self.trade_id,
            self.venue,
            self.ticker,
            self.resolved,
            self.venue_market_id,
            self.identity_status,
            self.quarantine_reason,
        )


@dataclass(frozen=True)
class IdentityMigrationPlan:
    fingerprint: str
    mapped: tuple[IdentityRow, ...]
    quarantine: tuple[IdentityRow, ...]
    unresolved: tuple[IdentityRow, ...]
    unchanged: tuple[IdentityRow, ...]

    def to_json(self) -> str:
        groups = {
            "mapped": self.mapped,
            "quarantined": self.quarantine,
            "unresolved": self.unresolved,
            "unchanged": self.unchanged,
        }
        payload = {
            "counts": {name: len(rows) for name, rows in groups.items()},
            "fingerprint": self.fingerprint,
            "rows": {
                name: [
                    {
                        key: value
                        for key, value in asdict(row).items()
                        if key
                        in {
                            "trade_id",
                            "venue",
                            "ticker",
                            "target_market_id",
                            "target_reason",
                        }
                    }
                    for row in rows
                ]
                for name, rows in groups.items()
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class PublicVenueIdentityResolver:
    def __init__(
        self,
        *,
        kalshi: KalshiRestClient | None = None,
        polymarket: PolymarketPublicClient | None = None,
    ) -> None:
        self._kalshi = kalshi or KalshiRestClient()
        self._polymarket = polymarket or PolymarketPublicClient()

    def lookup(self, venue: Venue, alias: str) -> IdentityLookup:
        if venue is Venue.KALSHI:
            try:
                market = self._kalshi.get_market(alias)
            except Exception:
                return IdentityLookup.transport()
            if market is None:
                return IdentityLookup.missing()
            returned = str(market.ticker or "").strip()
            if returned != alias:
                return IdentityLookup.mismatch("returned_ticker_mismatch")
            return IdentityLookup.mapped(returned)

        if venue is Venue.POLYMARKET_US:
            try:
                candidates = self._polymarket.find_market_payloads_by_slug_or_id(alias)
            except Exception:
                return IdentityLookup.transport()
            if not candidates:
                return IdentityLookup.missing()
            if len(candidates) != 1:
                return IdentityLookup.ambiguous()
            payload = candidates[0]
            returned_alias = str(payload.get("slug") or "").strip()
            canonical_id = str(payload.get("id") or "").strip()
            if returned_alias != alias:
                return IdentityLookup.mismatch("returned_slug_mismatch")
            if not canonical_id or not canonical_id.isdigit():
                return IdentityLookup.mismatch("invalid_numeric_market_id")
            return IdentityLookup.mapped(canonical_id)

        return IdentityLookup.unsupported()


@contextmanager
def open_readonly(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        yield conn
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(paper_trades)")}


def _load_open_rows(conn: sqlite3.Connection) -> list[IdentityRow]:
    columns = _columns(conn)
    optional = {
        name: name if name in columns else f"NULL AS {name}"
        for name in ("venue_market_id", "identity_status", "quarantine_reason")
    }
    rows = conn.execute(
        "SELECT trade_id, venue, ticker, resolved, "
        f"{optional['venue_market_id']}, {optional['identity_status']}, "
        f"{optional['quarantine_reason']} "
        "FROM paper_trades WHERE resolved=0 ORDER BY trade_id"
    ).fetchall()
    return [IdentityRow(*(row[key] for key in row.keys())) for row in rows]


def _plan_fingerprint(
    rows: list[IdentityRow],
    lookups: dict[tuple[str, str], IdentityLookup],
) -> str:
    payload = {
        "rows": [list(row.fingerprint_values()) for row in rows],
        "lookups": [
            [venue, alias, lookup.kind, lookup.canonical_id, lookup.reason]
            for (venue, alias), lookup in sorted(lookups.items())
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def plan_identity_migration(
    conn: sqlite3.Connection,
    resolver: IdentityResolver,
    *,
    reviewed_plan_fingerprint: str | None = None,
) -> IdentityMigrationPlan:
    rows = _load_open_rows(conn)
    grouped: dict[tuple[str, str], list[IdentityRow]] = {}
    lookups: dict[tuple[str, str], IdentityLookup] = {}
    for row in rows:
        try:
            venue = normalize_venue(row.venue)
        except ValueError:
            lookups[(row.venue, row.ticker)] = IdentityLookup.unsupported()
            grouped.setdefault((row.venue, row.ticker), []).append(row)
            continue
        key = (venue.value, row.ticker)
        grouped.setdefault(key, []).append(row)
        if key not in lookups:
            try:
                lookups[key] = resolver.lookup(venue, row.ticker)
            except Exception:
                lookups[key] = IdentityLookup.transport()

    fingerprint = _plan_fingerprint(rows, lookups)
    repeated_absence = reviewed_plan_fingerprint == fingerprint
    mapped: list[IdentityRow] = []
    quarantine: list[IdentityRow] = []
    unresolved: list[IdentityRow] = []
    unchanged: list[IdentityRow] = []
    deterministic = {"ambiguous", "mismatch", "unsupported"}

    for key, key_rows in sorted(grouped.items()):
        lookup = lookups[key]
        for row in key_rows:
            if row.identity_status == "quarantined":
                unchanged.append(row)
            elif lookup.kind == "mapped" and row.identity_status == "mapped":
                if row.venue_market_id == lookup.canonical_id:
                    unchanged.append(row)
                else:
                    quarantine.append(
                        IdentityRow(
                            *row.fingerprint_values(),
                            target_reason="existing_id_conflict",
                        )
                    )
            elif lookup.kind == "mapped":
                mapped.append(
                    IdentityRow(
                        *row.fingerprint_values(),
                        target_market_id=lookup.canonical_id,
                    )
                )
            elif lookup.kind in deterministic or (
                lookup.kind == "missing" and repeated_absence
            ):
                quarantine.append(
                    IdentityRow(
                        *row.fingerprint_values(),
                        target_reason=(
                            "confirmed_absence"
                            if lookup.kind == "missing"
                            else lookup.reason or lookup.kind
                        ),
                    )
                )
            else:
                unresolved.append(
                    IdentityRow(
                        *row.fingerprint_values(),
                        target_reason=lookup.reason or lookup.kind,
                    )
                )

    sort_key = lambda row: row.trade_id
    return IdentityMigrationPlan(
        fingerprint=fingerprint,
        mapped=tuple(sorted(mapped, key=sort_key)),
        quarantine=tuple(sorted(quarantine, key=sort_key)),
        unresolved=tuple(sorted(unresolved, key=sort_key)),
        unchanged=tuple(sorted(unchanged, key=sort_key)),
    )


def _ensure_identity_columns(
    conn: sqlite3.Connection,
    fault_hook: Callable[[str], None],
) -> None:
    columns = _columns(conn)
    definitions = (
        ("venue_market_id", "TEXT"),
        (
            "identity_status",
            "TEXT CHECK (identity_status IS NULL OR "
            "identity_status IN ('mapped', 'quarantined'))",
        ),
        ("quarantine_reason", "TEXT"),
    )
    for name, definition in definitions:
        if name not in columns:
            conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {name} {definition}")
            columns.add(name)
            fault_hook(f"after_ddl:{name}")


def _current_fingerprint(conn: sqlite3.Connection, trade_id: str) -> tuple[object, ...] | None:
    row = conn.execute(
        "SELECT trade_id, venue, ticker, resolved, venue_market_id, "
        "identity_status, quarantine_reason FROM paper_trades WHERE trade_id=?",
        (trade_id,),
    ).fetchone()
    return tuple(row) if row is not None else None


def apply_identity_plan(
    db_path: Path,
    plan: IdentityMigrationPlan,
    *,
    apply_quarantine: bool = False,
    reviewed_plan_fingerprint: str | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> None:
    if apply_quarantine and reviewed_plan_fingerprint != plan.fingerprint:
        raise ValueError("--apply-quarantine requires the reviewed plan fingerprint")
    hook = fault_hook or (lambda _stage: None)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_identity_columns(conn, hook)
        hook("after_ddl")
        planned_rows = (
            plan.mapped
            + plan.quarantine
            + plan.unresolved
            + plan.unchanged
        )
        for row in planned_rows:
            if _current_fingerprint(conn, row.trade_id) != row.fingerprint_values():
                raise RuntimeError(f"database drift for trade_id={row.trade_id}")
        actions = list(plan.mapped)
        if apply_quarantine:
            actions.extend(plan.quarantine)
        for row in actions:
            if row in plan.mapped:
                cursor = conn.execute(
                    "UPDATE paper_trades SET venue_market_id=?, identity_status='mapped', "
                    "quarantine_reason=NULL WHERE trade_id=? AND resolved=0",
                    (row.target_market_id, row.trade_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE paper_trades SET identity_status='quarantined', "
                    "quarantine_reason=? WHERE trade_id=? AND resolved=0",
                    (row.target_reason, row.trade_id),
                )
            if cursor.rowcount != 1:
                raise RuntimeError(f"database drift for trade_id={row.trade_id}")
            hook("after_update")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "paper_trades.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--apply-quarantine", action="store_true")
    parser.add_argument("--reviewed-plan-fingerprint")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    resolver = PublicVenueIdentityResolver()
    with open_readonly(args.db) as conn:
        plan = plan_identity_migration(
            conn,
            resolver,
            reviewed_plan_fingerprint=(
                args.reviewed_plan_fingerprint if args.apply_quarantine else None
            ),
        )
    print(plan.to_json())
    if args.apply or args.apply_quarantine:
        apply_identity_plan(
            args.db,
            plan,
            apply_quarantine=args.apply_quarantine,
            reviewed_plan_fingerprint=args.reviewed_plan_fingerprint,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
