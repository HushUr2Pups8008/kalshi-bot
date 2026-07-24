"""Regression tests for GDELT source timestamp handling."""

from datetime import datetime, timezone

import pytest

import feeds.gdelt_monitor as gdelt


def test_parse_seendate_returns_aware_utc_datetime() -> None:
    assert gdelt._parse_seendate("20260724T061530Z") == datetime(
        2026,
        7,
        24,
        6,
        15,
        30,
        tzinfo=timezone.utc,
    )


@pytest.mark.parametrize("seendate", [None, "", "not-a-gdelt-timestamp"])
def test_parse_seendate_returns_none_for_missing_or_invalid_values(seendate: object) -> None:
    assert gdelt._parse_seendate(seendate) is None


class StopAfterOneCycle(Exception):
    pass


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_monitor_propagates_missing_timestamp_to_callback(monkeypatch) -> None:
    delivered = []

    async def callback(item):
        delivered.append(item)

    async def fetch_one(_session, _query):
        return [
            {
                "url": "https://example.test/article",
                "title": "GDELT article without source timestamp",
                "seendate": "",
                "domain": "example.test",
            }
        ]

    async def stop_after_cycle(_seconds):
        raise StopAfterOneCycle

    monkeypatch.setattr(gdelt, "is_source_disabled", lambda _source: False)
    monkeypatch.setattr(gdelt, "_markets_to_queries", lambda _markets: ["test query"])
    monkeypatch.setattr(gdelt, "_fetch_gdelt_query", fetch_one)
    monkeypatch.setattr(gdelt, "_gdelt_query_limit", 1)
    monkeypatch.setattr(gdelt, "_gdelt_backoff_until", 0.0)
    monkeypatch.setattr(gdelt.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setattr(gdelt.asyncio, "sleep", stop_after_cycle)

    with pytest.raises(StopAfterOneCycle):
        await gdelt.run_gdelt_monitor(
            callback,
            get_markets=lambda: [object()],
            poll_interval=1,
        )

    assert len(delivered) == 1
    assert delivered[0].published is None
