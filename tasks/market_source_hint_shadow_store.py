"""Append-only evidence storage for source-hint shadow retrieval."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


_BUSY_TIMEOUT_SECONDS = 5.0
_MAX_READ_LIMIT = 1_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_source_hint_shadow_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    market_title TEXT NOT NULL,
    source_hint_query TEXT NOT NULL,
    source_hint_domain TEXT NOT NULL,
    feed_url TEXT NOT NULL,
    item_id TEXT NOT NULL,
    headline TEXT NOT NULL,
    item_url TEXT NOT NULL,
    item_source TEXT NOT NULL,
    published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_source_hint_shadow_observations_captured_at
    ON market_source_hint_shadow_observations(captured_at);
CREATE INDEX IF NOT EXISTS idx_market_source_hint_shadow_observations_ticker
    ON market_source_hint_shadow_observations(ticker);
CREATE TRIGGER IF NOT EXISTS immutable_market_source_hint_shadow_observations_update
BEFORE UPDATE ON market_source_hint_shadow_observations
BEGIN
    SELECT RAISE(ABORT, 'market source-hint shadow observations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS immutable_market_source_hint_shadow_observations_delete
BEFORE DELETE ON market_source_hint_shadow_observations
BEGIN
    SELECT RAISE(ABORT, 'market source-hint shadow observations are append-only');
END;
"""


@dataclass(frozen=True)
class MarketSourceHintShadowObservation:
    """Minimal provenance for one source-hint RSS observation."""

    captured_at: datetime
    ticker: str
    market_title: str
    source_hint_query: str
    source_hint_domain: str
    feed_url: str
    item_id: str
    headline: str
    item_url: str
    item_source: str
    published_at: datetime | None


class MarketSourceHintShadowStore:
    """Dedicated SQLite store with explicit initialization and read-only access."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._write_lock:
            await asyncio.to_thread(self._initialize_sync)

    async def append(self, observation: MarketSourceHintShadowObservation) -> None:
        _validate_observation(observation)
        async with self._write_lock:
            await asyncio.to_thread(self._append_sync, observation)

    def read_records(
        self,
        *,
        limit: int = 100,
    ) -> tuple[MarketSourceHintShadowObservation, ...]:
        """Return newest evidence records through a SQLite read-only connection."""
        if not self.db_path.is_file():
            return ()
        bounded_limit = max(1, min(int(limit), _MAX_READ_LIMIT))
        return self._read_records_sync(bounded_limit)

    def _connect_write(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_SECONDS * 1_000)}")
            connection.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            connection.close()
            raise
        return connection

    def _initialize_sync(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect_write()
        try:
            connection.executescript(_SCHEMA)
        finally:
            connection.close()

    def _append_sync(self, observation: MarketSourceHintShadowObservation) -> None:
        connection = self._connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO market_source_hint_shadow_observations (
                    captured_at, ticker, market_title, source_hint_query,
                    source_hint_domain, feed_url, item_id, headline, item_url,
                    item_source, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _observation_values(observation),
            )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _read_records_sync(
        self,
        limit: int,
    ) -> tuple[MarketSourceHintShadowObservation, ...]:
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            rows = connection.execute(
                """
                SELECT captured_at, ticker, market_title, source_hint_query,
                       source_hint_domain, feed_url, item_id, headline, item_url,
                       item_source, published_at
                FROM market_source_hint_shadow_observations
                ORDER BY observation_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return tuple(_observation_from_row(row) for row in rows)


def _validate_observation(observation: MarketSourceHintShadowObservation) -> None:
    if not isinstance(observation.captured_at, datetime):
        raise ValueError("captured_at must be a datetime")
    if observation.published_at is not None and not isinstance(
        observation.published_at, datetime
    ):
        raise ValueError("published_at must be a datetime or None")
    for name in (
        "ticker",
        "market_title",
        "source_hint_query",
        "source_hint_domain",
        "feed_url",
        "item_id",
        "headline",
        "item_url",
        "item_source",
    ):
        if not str(getattr(observation, name, "")).strip():
            raise ValueError(f"{name} must be non-empty")


def _observation_values(
    observation: MarketSourceHintShadowObservation,
) -> tuple[str, str, str, str, str, str, str, str, str, str, str | None]:
    return (
        _isoformat_utc(observation.captured_at),
        observation.ticker.strip(),
        observation.market_title.strip(),
        observation.source_hint_query.strip(),
        observation.source_hint_domain.strip(),
        observation.feed_url.strip(),
        observation.item_id.strip(),
        observation.headline.strip(),
        observation.item_url.strip(),
        observation.item_source.strip(),
        None
        if observation.published_at is None
        else _isoformat_utc(observation.published_at),
    )


def _observation_from_row(
    row: tuple[object, ...],
) -> MarketSourceHintShadowObservation:
    return MarketSourceHintShadowObservation(
        captured_at=_parse_datetime(row[0]),
        ticker=str(row[1]),
        market_title=str(row[2]),
        source_hint_query=str(row[3]),
        source_hint_domain=str(row[4]),
        feed_url=str(row[5]),
        item_id=str(row[6]),
        headline=str(row[7]),
        item_url=str(row[8]),
        item_source=str(row[9]),
        published_at=None if row[10] is None else _parse_datetime(row[10]),
    )


def _isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
