from __future__ import annotations

import asyncio
import json
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from scripts.brave_search_shadow_probe import (
    BRAVE_ENDPOINT,
    MAX_BYTES,
    MAX_QUERIES,
    TIMEOUT_SECONDS,
    ProbeInputError,
    main,
    run_probe,
)


def _input_file(
    tmp_path: Path,
    *,
    count: int = 1,
    query: str = "market-specific query",
) -> Path:
    path = tmp_path / "probe-input.jsonl"
    rows = [
        {
            "probe_window_id": f"window-{index % 2}",
            "ticker": f"TICKER-{index}",
            "research_run_id": f"run-{index}",
            "query": query,
        }
        for index in range(count)
    ]
    path.write_text("".join(f"{json.dumps(row)}\n" for row in rows), encoding="utf-8")
    return path


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_disabled_or_missing_key_makes_zero_transport_calls(tmp_path: Path) -> None:
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    result = asyncio.run(
        run_probe(
            _input_file(tmp_path),
            tmp_path / "probe.jsonl",
            enabled=False,
            api_key="",
            fetcher=fetcher,
        )
    )

    assert result.exit_code == 2
    assert calls == []
    assert not (tmp_path / "probe.jsonl").exists()


@pytest.mark.asyncio
async def test_successful_probe_writes_only_allowlisted_scalars(tmp_path: Path) -> None:
    secret = "brave-test-secret"
    query = "market-specific query"

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        return b'{"web":{"results":[{"title":"result-title","description":"result-snippet"},{"title":"two"}]}}'

    result = await run_probe(
        _input_file(tmp_path, query=query),
        tmp_path / "probe.jsonl",
        enabled=True,
        api_key=secret,
        fetcher=fetcher,
    )

    output_path = tmp_path / "probe.jsonl"
    rendered = output_path.read_text(encoding="utf-8")
    records = _records(output_path)
    assert result.exit_code == 0
    assert records[0].keys() == {
        "type",
        "shadow_only",
        "admission_path",
        "evidence_persisted",
        "paper_review_enqueued",
        "input_index",
        "probe_window_id",
        "ticker",
        "research_run_id",
        "provider",
        "outcome",
        "duration_ms",
        "http_status",
        "body_bytes",
        "schema_valid",
        "result_count",
        "error_class",
    }
    assert records[0] | {"duration_ms": 0} == {
        "type": "BRAVE_SEARCH_SHADOW_ATTEMPT",
        "shadow_only": True,
        "admission_path": "none",
        "evidence_persisted": False,
        "paper_review_enqueued": False,
        "input_index": 0,
        "probe_window_id": "window-0",
        "ticker": "TICKER-0",
        "research_run_id": "run-0",
        "provider": "brave_search",
        "outcome": "success",
        "duration_ms": 0,
        "http_status": None,
        "body_bytes": len(
            b'{"web":{"results":[{"title":"result-title","description":"result-snippet"},{"title":"two"}]}}'
        ),
        "schema_valid": True,
        "result_count": 2,
        "error_class": None,
    }
    assert records[1]["type"] == "BRAVE_SEARCH_SHADOW_SUMMARY"
    assert records[1]["attempts"] == 1
    assert records[1]["successes"] == 1
    assert records[1]["probe_window_ids"] == ["window-0"]
    assert records[1]["p95_duration_ms"] == records[0]["duration_ms"]
    assert records[1]["p95_success_duration_ms"] == records[0]["duration_ms"]
    for forbidden in (
        secret,
        query,
        "X-Subscription-Token",
        "result-title",
        "result-snippet",
        "https://",
    ):
        assert forbidden not in rendered


@pytest.mark.asyncio
async def test_timeout_is_sanitized_and_next_input_runs(tmp_path: Path) -> None:
    async def fetcher(*args: object, **kwargs: object) -> bytes:
        raise TimeoutError("brave-test-secret must not be stored")

    result = await run_probe(
        _input_file(tmp_path, count=2),
        tmp_path / "probe.jsonl",
        enabled=True,
        api_key="brave-test-secret",
        fetcher=fetcher,
    )

    rendered = (tmp_path / "probe.jsonl").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert rendered.count('"outcome":"timeout"') == 2
    assert '"error_class":"TimeoutError"' in rendered
    assert "brave-test-secret" not in rendered


@pytest.mark.asyncio
async def test_input_is_fully_validated_before_any_transport_call(tmp_path: Path) -> None:
    input_path = _input_file(tmp_path, count=1)
    with input_path.open("a", encoding="utf-8") as handle:
        handle.write('{"probe_window_id":"window-1"}\n')
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    with pytest.raises(ProbeInputError):
        await run_probe(
            input_path,
            tmp_path / "probe.jsonl",
            enabled=True,
            api_key="brave-test-secret",
            fetcher=fetcher,
        )

    assert calls == []
    assert not (tmp_path / "probe.jsonl").exists()


@pytest.mark.asyncio
async def test_probe_uses_the_pinned_bounded_transport_in_serial_order(tmp_path: Path) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    in_flight = 0
    max_in_flight = 0

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        calls.append((args, kwargs))
        await asyncio.sleep(0)
        in_flight -= 1
        return b'{"web":{"results":[]}}'

    await run_probe(
        _input_file(tmp_path, count=2),
        tmp_path / "probe.jsonl",
        enabled=True,
        api_key="brave-test-secret",
        fetcher=fetcher,
    )

    assert max_in_flight == 1
    assert len(calls) == 2
    for args, kwargs in calls:
        assert args[0].startswith(f"{BRAVE_ENDPOINT}?")
        assert kwargs == {
            "canonical_host": "api.search.brave.com",
            "provider_name": "Brave Search API Shadow",
            "user_agent": "kalshi-bot-brave-shadow/1.0",
            "timeout": TIMEOUT_SECONDS,
            "max_bytes": MAX_BYTES,
            "request_headers": {
                "Accept": "application/json",
                "X-Subscription-Token": "brave-test-secret",
            },
        }
        assert "q=market-specific+query" in args[0]
        assert "count=3" in args[0]


@pytest.mark.asyncio
async def test_http_and_malformed_responses_are_sanitized(tmp_path: Path) -> None:
    calls = 0

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                "https://api.search.brave.com/private?brave-test-secret",
                429,
                "brave-test-secret",
                hdrs=None,
                fp=BytesIO(),
            )
        return b"not-json result-title"

    await run_probe(
        _input_file(tmp_path, count=2),
        tmp_path / "probe.jsonl",
        enabled=True,
        api_key="brave-test-secret",
        fetcher=fetcher,
    )

    rendered = (tmp_path / "probe.jsonl").read_text(encoding="utf-8")
    records = _records(tmp_path / "probe.jsonl")
    assert records[0]["outcome"] == "http_error"
    assert records[0]["http_status"] == 429
    assert records[0]["error_class"] == "HTTPError"
    assert records[1]["outcome"] == "malformed_response"
    assert records[1]["error_class"] == "JSONDecodeError"
    for forbidden in ("brave-test-secret", "result-title", "https://"):
        assert forbidden not in rendered


@pytest.mark.asyncio
async def test_non_object_web_response_is_a_sanitized_malformed_record(tmp_path: Path) -> None:
    async def fetcher(*args: object, **kwargs: object) -> bytes:
        return b'{"web":["result-title"]}'

    result = await run_probe(
        _input_file(tmp_path),
        tmp_path / "probe.jsonl",
        enabled=True,
        api_key="brave-test-secret",
        fetcher=fetcher,
    )

    rendered = (tmp_path / "probe.jsonl").read_text(encoding="utf-8")
    record = _records(tmp_path / "probe.jsonl")[0]
    assert result.exit_code == 0
    assert record["outcome"] == "malformed_response"
    assert record["schema_valid"] is False
    assert record["error_class"] == "ProviderError"
    assert "result-title" not in rendered


def test_probe_has_no_runtime_admission_imports() -> None:
    source = Path("scripts/brave_search_shadow_probe.py").read_text(encoding="utf-8")
    forbidden = (
        "import main",
        "analysis.research_gate",
        "ResearchPrewarmTask",
        "run_research_gate",
        "dossier",
        "paper_admission",
        "TradeLogger",
    )
    assert not any(token in source for token in forbidden)


@pytest.mark.asyncio
async def test_probe_rejects_more_than_thirty_rows_before_transport(tmp_path: Path) -> None:
    with pytest.raises(ProbeInputError, match="30"):
        await run_probe(
            _input_file(tmp_path, count=MAX_QUERIES + 1),
            tmp_path / "probe.jsonl",
            enabled=True,
            api_key="brave-test-secret",
        )


def test_cli_requires_all_activation_conditions_and_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    input_path = _input_file(tmp_path)
    output_path = tmp_path / "probe.jsonl"
    monkeypatch.setenv("ENABLE_BRAVE_SEARCH_SHADOW", "true")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-test-secret")

    assert main(["--input", str(input_path), "--output", str(output_path)]) == 2
    missing_execute_output = capsys.readouterr().out
    assert "brave-test-secret" not in missing_execute_output
    assert "market-specific query" not in missing_execute_output
    assert not output_path.exists()

    assert main(["--execute", "--input", "relative.jsonl", "--output", str(output_path)]) == 2
    assert not output_path.exists()

    monkeypatch.setenv("ENABLE_BRAVE_SEARCH_SHADOW", "false")
    assert main(["--execute", "--input", str(input_path), "--output", str(output_path)]) == 2
    disabled_output = capsys.readouterr().out
    assert "brave-test-secret" not in disabled_output
    assert "market-specific query" not in disabled_output
    assert not output_path.exists()
