from __future__ import annotations

import asyncio
import json
import os
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from scripts import brave_search_shadow_probe
from scripts.brave_search_shadow_probe import (
    BRAVE_ENDPOINT,
    MAX_BYTES,
    MAX_QUERIES,
    TIMEOUT_SECONDS,
    ProbeInputError,
    main,
    run_probe,
)


def test_research_shadow_env_example_marks_brave_probe_as_operator_only() -> None:
    rendered = Path("docs/governance/research-shadow.env.example").read_text(
        encoding="utf-8"
    )

    assert "ENABLE_BRAVE_SEARCH_SHADOW=false" in rendered
    assert "BRAVE_SEARCH_API_KEY=" in rendered
    assert "operator-only" in rendered
    assert "does not enable runtime research or admission" in rendered


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
    assert "probe_window_ids" not in records[1]
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
@pytest.mark.parametrize("destination_kind", ("missing_parent", "directory"))
async def test_unusable_output_destination_prevents_transport_calls(
    tmp_path: Path, destination_kind: str
) -> None:
    if destination_kind == "missing_parent":
        output_path = tmp_path / "missing" / "probe.jsonl"
    else:
        output_path = tmp_path / "output-directory"
        output_path.mkdir()
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    with pytest.raises(ProbeInputError, match="output destination"):
        await run_probe(
            _input_file(tmp_path),
            output_path,
            enabled=True,
            api_key="brave-test-secret",
            fetcher=fetcher,
        )

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", (0o720, 0o702), ids=("group_writable", "world_writable"))
async def test_untrusted_output_parent_prevents_transport_calls(
    tmp_path: Path, mode: int
) -> None:
    output_parent = tmp_path / "untrusted-output"
    output_parent.mkdir(mode=0o700)
    output_parent.chmod(mode)
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    try:
        with pytest.raises(ProbeInputError, match="output destination"):
            await run_probe(
                _input_file(tmp_path),
                output_parent / "probe.jsonl",
                enabled=True,
                api_key="brave-test-secret",
                fetcher=fetcher,
            )
    finally:
        output_parent.chmod(0o700)

    assert calls == []


@pytest.mark.asyncio
async def test_malformed_output_path_prevents_transport_calls(tmp_path: Path) -> None:
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    malformed_output_path = Path(f"{tmp_path}/probe\x00.jsonl")
    with pytest.raises(ProbeInputError, match="output destination"):
        await run_probe(
            _input_file(tmp_path),
            malformed_output_path,
            enabled=True,
            api_key="brave-test-secret",
            fetcher=fetcher,
        )

    assert calls == []


@pytest.mark.asyncio
async def test_symlink_output_destination_prevents_transport_calls(tmp_path: Path) -> None:
    target_path = tmp_path / "target.jsonl"
    target_path.write_text("prior artifact\n", encoding="utf-8")
    output_path = tmp_path / "probe.jsonl"
    output_path.symlink_to(target_path)
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    with pytest.raises(ProbeInputError, match="output destination"):
        await run_probe(
            _input_file(tmp_path),
            output_path,
            enabled=True,
            api_key="brave-test-secret",
            fetcher=fetcher,
        )

    assert calls == []
    assert target_path.read_text(encoding="utf-8") == "prior artifact\n"


@pytest.mark.asyncio
async def test_resolved_output_parent_resists_parent_symlink_swap(tmp_path: Path) -> None:
    trusted_parent = tmp_path / "trusted-output"
    alternate_parent = tmp_path / "alternate-output"
    trusted_parent.mkdir(mode=0o700)
    alternate_parent.mkdir(mode=0o700)
    alias_parent = tmp_path / "output-alias"
    alias_parent.symlink_to(trusted_parent, target_is_directory=True)

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        alias_parent.unlink()
        alias_parent.symlink_to(alternate_parent, target_is_directory=True)
        return b'{"web":{"results":[]}}'

    result = await run_probe(
        _input_file(tmp_path),
        alias_parent / "probe.jsonl",
        enabled=True,
        api_key="brave-test-secret",
        fetcher=fetcher,
    )

    assert result.exit_code == 0
    assert (trusted_parent / "probe.jsonl").exists()
    assert not (alternate_parent / "probe.jsonl").exists()


@pytest.mark.asyncio
async def test_probe_stages_output_then_atomically_replaces_destination(tmp_path: Path) -> None:
    output_path = tmp_path / "probe.jsonl"
    output_path.write_text("previous-artifact\n", encoding="utf-8")
    staged_paths: list[Path] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        staged_paths.extend(tmp_path.glob(".brave-search-shadow-*.tmp"))
        assert output_path.read_text(encoding="utf-8") == "previous-artifact\n"
        return b'{"web":{"results":[]}}'

    with output_path.open(encoding="utf-8") as prior_artifact:
        result = await run_probe(
            _input_file(tmp_path),
            output_path,
            enabled=True,
            api_key="brave-test-secret",
            fetcher=fetcher,
        )
        prior_artifact.seek(0)
        assert prior_artifact.read() == "previous-artifact\n"

    assert result.exit_code == 0
    assert len(staged_paths) == 1
    assert staged_paths[0].parent == output_path.parent
    assert staged_paths[0] != output_path
    assert output_path.read_text(encoding="utf-8") != "previous-artifact\n"
    assert list(tmp_path.glob(".brave-search-shadow-*.tmp")) == []


@pytest.mark.asyncio
async def test_staging_descriptor_lives_through_transport_and_closes_after_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reservations: list[object] = []
    tracked_outputs: list[object] = []
    original_reserve_output = brave_search_shadow_probe._reserve_output
    original_fdopen = brave_search_shadow_probe.os.fdopen

    def reserve_output(path: Path) -> object:
        reservation = original_reserve_output(path)
        reservations.append(reservation)
        return reservation

    class TrackingOutput:
        def __init__(self, wrapped: Any) -> None:
            self.wrapped = wrapped
            self.closed = False

        def __enter__(self) -> TrackingOutput:
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            self.closed = True
            return self.wrapped.__exit__(*args)

        def write(self, rendered: str) -> int:
            return self.wrapped.write(rendered)

        def flush(self) -> None:
            self.wrapped.flush()

        def fileno(self) -> int:
            return self.wrapped.fileno()

    def tracking_fdopen(*args: object, **kwargs: object) -> TrackingOutput:
        tracked_output = TrackingOutput(original_fdopen(*args, **kwargs))
        tracked_outputs.append(tracked_output)
        return tracked_output

    monkeypatch.setattr(brave_search_shadow_probe, "_reserve_output", reserve_output)
    monkeypatch.setattr(brave_search_shadow_probe.os, "fdopen", tracking_fdopen)

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        assert len(reservations) == 1
        descriptor = reservations[0].descriptor
        assert descriptor is not None
        os.fstat(descriptor)
        assert tracked_outputs == []
        return b'{"web":{"results":[]}}'

    result = await run_probe(
        _input_file(tmp_path),
        tmp_path / "probe.jsonl",
        enabled=True,
        api_key="brave-test-secret",
        fetcher=fetcher,
    )

    assert result.exit_code == 0
    assert reservations[0].descriptor is None
    assert len(tracked_outputs) == 1
    assert tracked_outputs[0].closed is True


@pytest.mark.asyncio
async def test_key_and_query_shaped_identifiers_never_persisted(tmp_path: Path) -> None:
    identifier_values = {
        "probe_window_id": "BRAVE_SEARCH_API_KEY=identifier-secret",
        "ticker": "query-shaped identifier: market terms",
        "research_run_id": "q=identifier-search&not=output",
    }
    input_path = _input_file(tmp_path, query="probe-query-distinct-from-identifiers")
    rows = [
        json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines()
    ]
    rows[0].update(identifier_values)
    input_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    result = await run_probe(
        input_path,
        tmp_path / "probe.jsonl",
        enabled=True,
        api_key="brave-test-secret",
        fetcher=fetcher,
    )

    rendered = (tmp_path / "probe.jsonl").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert len(calls) == 1
    assert all(value not in calls[0][0][0] for value in identifier_values.values())
    assert all(value not in rendered for value in identifier_values.values())
    assert "probe_window_id" not in rendered
    assert "research_run_id" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("probe_window_id", "window\x00identifier"),
        ("ticker", "\ud800"),
        ("research_run_id", "R" * 10_000),
    ),
    ids=("control", "lone_surrogate", "overlong"),
)
async def test_invalid_identifier_is_rejected_before_transport(
    tmp_path: Path, field: str, invalid_value: str
) -> None:
    input_path = _input_file(tmp_path, count=1)
    rows = [
        json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines()
    ]
    rows[0][field] = invalid_value
    input_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    with pytest.raises(ProbeInputError, match="invalid identifier"):
        await run_probe(
            input_path,
            tmp_path / "probe.jsonl",
            enabled=True,
            api_key="brave-test-secret",
            fetcher=fetcher,
        )

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_query", ("\ud800", "market\x00specific-query"), ids=("lone_surrogate", "control"),
)
async def test_invalid_query_rejected_before_transport(
    tmp_path: Path, invalid_query: str
) -> None:
    input_path = _input_file(tmp_path, count=1)
    with input_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "probe_window_id": "window-1",
                    "ticker": "TICKER-1",
                    "research_run_id": "run-1",
                    "query": invalid_query,
                }
            )
            + "\n"
        )
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    with pytest.raises(ProbeInputError, match="invalid query"):
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
async def test_over_limit_printable_query_is_rejected_before_transport(tmp_path: Path) -> None:
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    with pytest.raises(ProbeInputError, match="query"):
        await run_probe(
            _input_file(tmp_path, query="q" * 10_000),
            tmp_path / "probe.jsonl",
            enabled=True,
            api_key="brave-test-secret",
            fetcher=fetcher,
        )

    assert calls == []
    assert not (tmp_path / "probe.jsonl").exists()


@pytest.mark.asyncio
async def test_over_limit_encoded_url_is_rejected_before_transport(tmp_path: Path) -> None:
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    with pytest.raises(ProbeInputError, match="URL"):
        await run_probe(
            _input_file(tmp_path, query="\U0001f600" * 500),
            tmp_path / "probe.jsonl",
            enabled=True,
            api_key="brave-test-secret",
            fetcher=fetcher,
        )

    assert calls == []
    assert not (tmp_path / "probe.jsonl").exists()


@pytest.mark.asyncio
async def test_normal_cohort_market_and_run_identifiers_are_accepted(tmp_path: Path) -> None:
    input_path = tmp_path / "probe-input.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "probe_window_id": "legacy-pending-20260729",
                "ticker": "KXFISAEXTEND-26MAY-JUN15",
                "research_run_id": "rr-prewarm-20260729T034240Z",
                "query": "market-specific query",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = await run_probe(
        input_path,
        tmp_path / "probe.jsonl",
        enabled=True,
        api_key="brave-test-secret",
        fetcher=lambda *args, **kwargs: asyncio.sleep(0, result=b'{"web":{"results":[]}}'),
    )

    assert result.exit_code == 0


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
