import asyncio
import errno
import json
import socket
import subprocess
import sys
import textwrap
import urllib.error
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

import config as config_module
from analysis import research_gate as research_gate_module
from analysis.generic_search_circuit import (
    GenericSearchCircuit,
    GenericSearchCircuitEvent,
    GenericSearchUnavailable,
    is_provider_availability_failure,
)
from analysis.research_gate import ResearchEvidence, ResearchQuery, default_search_provider


@pytest.fixture(autouse=True)
def _reset_generic_search_circuit() -> None:
    research_gate_module._reset_generic_search_circuit_for_tests()
    yield
    research_gate_module._reset_generic_search_circuit_for_tests()


def _valid_rsa_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _bot_config() -> config_module.BotConfig:
    return config_module.BotConfig(
        api_key_id="kalshi-key",
        api_key_secret=_valid_rsa_pem(),
    )


def test_generic_search_circuit_mode_defaults_to_shadow(monkeypatch) -> None:
    monkeypatch.delenv("GENERIC_SEARCH_CIRCUIT_MODE", raising=False)

    cfg = _bot_config()

    assert cfg.generic_search_circuit_mode == "shadow"


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_generic_search_circuit_mode_accepts_exact_values(monkeypatch, mode: str) -> None:
    monkeypatch.setenv("GENERIC_SEARCH_CIRCUIT_MODE", mode)

    cfg = _bot_config()

    assert cfg.generic_search_circuit_mode == mode


@pytest.mark.parametrize("mode", ["disabled", "SHADOW", "Enforce"])
def test_generic_search_circuit_mode_rejects_other_or_case_mutated_values(
    monkeypatch,
    capsys,
    mode: str,
) -> None:
    monkeypatch.setenv("GENERIC_SEARCH_CIRCUIT_MODE", mode)

    with pytest.raises(SystemExit):
        _bot_config()

    assert (
        "GENERIC_SEARCH_CIRCUIT_MODE must be one of off|shadow|enforce"
        in capsys.readouterr().err
    )


def _generic_evidence(query: ResearchQuery, *, source_name: str) -> ResearchEvidence:
    return ResearchEvidence(
        source_class=query.source_class,
        source_name=source_name,
        source_url=f"https://example.com/{source_name.lower()}",
        title=f"{source_name} result",
        snippet="Current result.",
        claim_type=query.query_intent,
    )


@pytest.mark.asyncio
async def test_generic_search_runner_returns_rss_success_without_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(research_gate_module.cfg, "generic_search_circuit_mode", "enforce")
    query = ResearchQuery("private query", "supporting", "reputable_secondary")
    expected = [_generic_evidence(query, source_name="RSS")]
    calls: list[str] = []

    def rss_search(_query: ResearchQuery) -> list[ResearchEvidence]:
        calls.append("rss")
        return expected

    def fallback_search(_query: ResearchQuery) -> list[ResearchEvidence]:
        calls.append("ddg")
        raise AssertionError("fallback must not run after RSS success")

    monkeypatch.setattr(research_gate_module, "_rss_search", rss_search)
    monkeypatch.setattr(
        research_gate_module,
        "_duckduckgo_lite_search",
        fallback_search,
    )

    assert await research_gate_module._run_generic_search(query) == expected
    assert calls == ["rss"]
    assert research_gate_module._get_generic_search_circuit().snapshot().state == "closed"


@pytest.mark.asyncio
async def test_generic_search_runner_opens_after_two_availability_failures(
    monkeypatch,
) -> None:
    monkeypatch.setattr(research_gate_module.cfg, "generic_search_circuit_mode", "enforce")
    query = ResearchQuery("private query", "supporting", "reputable_secondary")
    calls: list[str] = []

    def rss_search(_query: ResearchQuery) -> list[ResearchEvidence]:
        calls.append("rss")
        raise TimeoutError("private RSS failure")

    def fallback_search(_query: ResearchQuery) -> list[ResearchEvidence]:
        calls.append("ddg")
        raise ConnectionError("private DDG failure")

    monkeypatch.setattr(research_gate_module, "_rss_search", rss_search)
    monkeypatch.setattr(
        research_gate_module,
        "_duckduckgo_lite_search",
        fallback_search,
    )

    with pytest.raises(GenericSearchUnavailable):
        await research_gate_module._run_generic_search(query)

    snapshot = research_gate_module._get_generic_search_circuit().snapshot()
    assert calls == ["rss", "ddg"]
    assert snapshot.state == "open"
    assert snapshot.generation == 1
    assert snapshot.double_availability_failures == 1


@pytest.mark.asyncio
async def test_generic_search_runner_parser_failure_uses_fallback_without_mutation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(research_gate_module.cfg, "generic_search_circuit_mode", "enforce")
    query = ResearchQuery("private query", "supporting", "reputable_secondary")
    expected = [_generic_evidence(query, source_name="DDG")]
    calls: list[str] = []

    def rss_search(_query: ResearchQuery) -> list[ResearchEvidence]:
        calls.append("rss")
        raise ET.ParseError("private response body")

    def fallback_search(_query: ResearchQuery) -> list[ResearchEvidence]:
        calls.append("ddg")
        return expected

    monkeypatch.setattr(research_gate_module, "_rss_search", rss_search)
    monkeypatch.setattr(
        research_gate_module,
        "_duckduckgo_lite_search",
        fallback_search,
    )

    assert await research_gate_module._run_generic_search(query) == expected
    snapshot = research_gate_module._get_generic_search_circuit().snapshot()
    assert calls == ["rss", "ddg"]
    assert snapshot.state == "closed"
    assert snapshot.generation == 0
    assert snapshot.double_availability_failures == 0


@pytest.mark.asyncio
async def test_structured_evidence_returns_while_generic_circuit_is_open(
    monkeypatch,
) -> None:
    monkeypatch.setattr(research_gate_module.cfg, "generic_search_circuit_mode", "enforce")
    query = ResearchQuery("private query", "supporting", "reputable_secondary")
    structured = [_generic_evidence(query, source_name="Official")]

    monkeypatch.setattr(
        research_gate_module,
        "_rss_search",
        lambda _query: (_ for _ in ()).throw(TimeoutError("RSS unavailable")),
    )
    monkeypatch.setattr(
        research_gate_module,
        "_duckduckgo_lite_search",
        lambda _query: (_ for _ in ()).throw(ConnectionError("DDG unavailable")),
    )
    with pytest.raises(GenericSearchUnavailable):
        await research_gate_module._run_generic_search(query)

    monkeypatch.setattr(
        research_gate_module,
        "_truth_social_event_search",
        lambda _query: structured,
    )

    assert await default_search_provider(query) == structured
    assert research_gate_module._get_generic_search_circuit().snapshot().state == "open"


def test_autouse_reset_fixture_isolates_open_generation_in_subprocess(tmp_path) -> None:
    regression = tmp_path / "test_generic_circuit_reset_boundary.py"
    regression.write_text(
        textwrap.dedent(
            """
            import pytest

            from analysis import research_gate as research_gate_module
            from analysis.generic_search_circuit import GenericSearchUnavailable


            _OPEN_CIRCUIT = None


            @pytest.fixture(autouse=True)
            def _reset_generic_search_circuit():
                research_gate_module._reset_generic_search_circuit_for_tests()
                yield
                research_gate_module._reset_generic_search_circuit_for_tests()


            @pytest.mark.asyncio
            async def test_open_generation(monkeypatch):
                global _OPEN_CIRCUIT
                monkeypatch.setattr(
                    research_gate_module.cfg,
                    "generic_search_circuit_mode",
                    "enforce",
                )
                circuit = research_gate_module._get_generic_search_circuit()

                async def fail(exc):
                    raise exc

                with pytest.raises(GenericSearchUnavailable):
                    await circuit.run(
                        lambda: fail(TimeoutError("RSS unavailable")),
                        lambda: fail(ConnectionError("DDG unavailable")),
                    )
                assert circuit.snapshot().generation == 1
                _OPEN_CIRCUIT = circuit


            def test_next_boundary_starts_closed():
                assert _OPEN_CIRCUIT is not None
                current = research_gate_module._get_generic_search_circuit()
                assert current is not _OPEN_CIRCUIT
                assert current.snapshot().state == "closed"
                assert current.snapshot().generation == 0
            """
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(regression), "-q"],
        check=False,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "2 passed" in completed.stdout


@pytest.mark.asyncio
async def test_repository_circuit_event_logging_is_sanitized(monkeypatch) -> None:
    monkeypatch.setattr(research_gate_module.cfg, "generic_search_circuit_mode", "enforce")
    secret_query = "private query token"
    secret_rss = "private RSS response"
    secret_ddg = "https://private.example/ddg"
    captured: list[tuple[str, tuple[Any, ...]]] = []

    monkeypatch.setattr(
        research_gate_module.log,
        "warning",
        lambda message, *args: captured.append((message, args)),
    )
    monkeypatch.setattr(
        research_gate_module,
        "_rss_search",
        lambda _query: (_ for _ in ()).throw(TimeoutError(secret_rss)),
    )
    monkeypatch.setattr(
        research_gate_module,
        "_duckduckgo_lite_search",
        lambda _query: (_ for _ in ()).throw(ConnectionError(secret_ddg)),
    )

    with pytest.raises(GenericSearchUnavailable):
        await research_gate_module._run_generic_search(
            ResearchQuery(secret_query, "supporting", "reputable_secondary")
        )

    assert len(captured) == 1
    message, args = captured[0]
    assert message == (
        "generic_search_circuit kind=%s mode=%s state=%s generation=%d "
        "failure_classes=%s cooldown_seconds=%.3f remaining_cooldown_seconds=%.3f"
    )
    assert args[:5] == ("open", "enforce", "open", 1, "TimeoutError,ConnectionError")
    rendered = f"{message} {args}"
    assert secret_query not in rendered
    assert secret_rss not in rendered
    assert secret_ddg not in rendered


@pytest.mark.asyncio
async def test_repository_logger_failure_cannot_change_circuit_result(monkeypatch) -> None:
    monkeypatch.setattr(research_gate_module.cfg, "generic_search_circuit_mode", "enforce")
    query = ResearchQuery("private query", "supporting", "reputable_secondary")

    def fail_logging(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("logger unavailable")

    monkeypatch.setattr(research_gate_module.log, "warning", fail_logging)
    monkeypatch.setattr(
        research_gate_module,
        "_rss_search",
        lambda _query: (_ for _ in ()).throw(TimeoutError("RSS unavailable")),
    )
    monkeypatch.setattr(
        research_gate_module,
        "_duckduckgo_lite_search",
        lambda _query: (_ for _ in ()).throw(ConnectionError("DDG unavailable")),
    )

    with pytest.raises(GenericSearchUnavailable):
        await research_gate_module._run_generic_search(query)
    assert research_gate_module._get_generic_search_circuit().snapshot().state == "open"


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://provider.invalid/private-query",
        code=status,
        msg="provider payload",
        hdrs=None,
        fp=None,
    )


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        pytest.param(TimeoutError("timed out"), True, id="timeout-error"),
        pytest.param(asyncio.TimeoutError("timed out"), True, id="asyncio-timeout"),
        pytest.param(socket.gaierror(-2, "dns failed"), True, id="dns-error"),
        pytest.param(ConnectionError("connection failed"), True, id="connection-error"),
        pytest.param(
            OSError(errno.ENETUNREACH, "network unreachable"),
            True,
            id="network-os-error",
        ),
        pytest.param(
            urllib.error.URLError(TimeoutError("timed out")),
            True,
            id="url-timeout",
        ),
        pytest.param(
            urllib.error.URLError(socket.gaierror(-2, "dns failed")),
            True,
            id="url-dns-error",
        ),
        pytest.param(
            urllib.error.URLError(OSError(errno.ECONNREFUSED, "refused")),
            True,
            id="url-connection-os-error",
        ),
        pytest.param(_http_error(429), True, id="http-429"),
        pytest.param(_http_error(500), True, id="http-500"),
        pytest.param(_http_error(503), True, id="http-503"),
        pytest.param(_http_error(400), False, id="http-400"),
        pytest.param(_http_error(401), False, id="http-401"),
        pytest.param(_http_error(403), False, id="http-403"),
        pytest.param(_http_error(404), False, id="http-404"),
        pytest.param(urllib.error.URLError("timed out"), False, id="url-string-reason"),
        pytest.param(
            urllib.error.URLError(ValueError("bad value")),
            False,
            id="url-programming-error",
        ),
        pytest.param(
            urllib.error.URLError(FileNotFoundError("missing file")),
            False,
            id="url-local-os-error",
        ),
        pytest.param(ValueError("bad value"), False, id="value-error"),
        pytest.param(AssertionError("bad assertion"), False, id="assertion-error"),
        pytest.param(ET.ParseError("malformed xml"), False, id="xml-parse-error"),
        pytest.param(
            json.JSONDecodeError("malformed json", "{", 1),
            False,
            id="json-parse-error",
        ),
    ],
)
def test_provider_availability_failure_taxonomy(
    exc: BaseException,
    expected: bool,
) -> None:
    assert is_provider_availability_failure(exc) is expected


def _raising_provider(
    label: str,
    calls: list[str],
    exc: BaseException,
) -> Callable[[], Awaitable[Any]]:
    async def provider() -> Any:
        calls.append(label)
        raise exc

    return provider


def _successful_provider(
    label: str,
    calls: list[str],
    result: Any,
) -> Callable[[], Awaitable[Any]]:
    async def provider() -> Any:
        calls.append(label)
        return result

    return provider


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _controlled_provider(
    label: str,
    calls: list[str],
    future: asyncio.Future[Any],
    started: asyncio.Event | None = None,
) -> Callable[[], Awaitable[Any]]:
    async def provider() -> Any:
        calls.append(label)
        if started is not None:
            started.set()
        return await future

    return provider


async def _open_circuit(
    circuit: GenericSearchCircuit,
    calls: list[str] | None = None,
    *,
    primary_error: BaseException | None = None,
    fallback_error: BaseException | None = None,
) -> None:
    provider_calls = calls if calls is not None else []
    with pytest.raises(GenericSearchUnavailable):
        await circuit.run(
            _raising_provider(
                "rss",
                provider_calls,
                primary_error or TimeoutError("rss unavailable"),
            ),
            _raising_provider(
                "ddg",
                provider_calls,
                fallback_error or ConnectionError("ddg unavailable"),
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
async def test_closed_modes_fall_back_after_every_primary_exception(mode: str) -> None:
    calls: list[str] = []
    fallback_result = object()
    circuit = GenericSearchCircuit(mode=mode)

    result = await circuit.run(
        _raising_provider("rss", calls, ValueError("malformed RSS")),
        _successful_provider("ddg", calls, fallback_result),
    )

    assert calls == ["rss", "ddg"]
    assert result is fallback_result
    assert circuit.snapshot().state == "closed"


@pytest.mark.asyncio
async def test_non_availability_primary_failure_does_not_mutate_circuit() -> None:
    calls: list[str] = []
    fallback_result = object()
    circuit = GenericSearchCircuit(mode="enforce")

    result = await circuit.run(
        _raising_provider("rss", calls, ET.ParseError("private query payload")),
        _successful_provider("ddg", calls, fallback_result),
    )

    snapshot = circuit.snapshot()
    assert calls == ["rss", "ddg"]
    assert result is fallback_result
    assert snapshot.state == "closed"
    assert snapshot.double_availability_failures == 0
    assert snapshot.open_transitions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "open_transitions", "would_open_transitions", "expected_state"),
    [
        pytest.param("off", 0, 0, "closed", id="off"),
        pytest.param("shadow", 0, 1, "open", id="shadow"),
        pytest.param("enforce", 1, 0, "open", id="enforce"),
    ],
)
async def test_double_availability_failure_is_sanitized(
    mode: str,
    open_transitions: int,
    would_open_transitions: int,
    expected_state: str,
) -> None:
    calls: list[str] = []
    events: list[GenericSearchCircuitEvent] = []
    circuit = GenericSearchCircuit(
        mode=mode,
        cooldown_seconds=30.0,
        clock=lambda: 100.0,
        event_sink=events.append,
    )
    primary_secret = "forecast query secret"
    fallback_secret = "https://provider.invalid/private?q=secret"
    primary_error = TimeoutError(primary_secret)
    fallback_error = urllib.error.HTTPError(
        url=fallback_secret,
        code=503,
        msg="private response payload",
        hdrs=None,
        fp=None,
    )

    with pytest.raises(GenericSearchUnavailable) as raised:
        await circuit.run(
            _raising_provider("rss", calls, primary_error),
            _raising_provider("ddg", calls, fallback_error),
        )

    snapshot = circuit.snapshot()
    diagnostic = str(raised.value)
    assert calls == ["rss", "ddg"]
    assert snapshot.state == expected_state
    assert snapshot.last_failure_classes == ("TimeoutError", "HTTPError:503")
    assert snapshot.double_availability_failures == 1
    assert snapshot.open_transitions == open_transitions
    assert snapshot.would_open_transitions == would_open_transitions
    assert "TimeoutError" in diagnostic
    assert "HTTPError:503" in diagnostic
    for secret in (primary_secret, fallback_secret, "private response payload"):
        assert secret not in diagnostic
        assert all(secret not in repr(event) for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary_error", "fallback_error"),
    [
        pytest.param(
            TimeoutError("rss unavailable"),
            ValueError("ddg parser failed"),
            id="eligible-then-ineligible",
        ),
        pytest.param(
            ValueError("rss parser failed"),
            TimeoutError("ddg unavailable"),
            id="ineligible-then-eligible",
        ),
        pytest.param(
            AssertionError("rss invariant"),
            ET.ParseError("ddg malformed response"),
            id="double-ineligible",
        ),
    ],
)
async def test_non_double_availability_failure_propagates_fallback_exception(
    primary_error: BaseException,
    fallback_error: BaseException,
) -> None:
    calls: list[str] = []
    circuit = GenericSearchCircuit(mode="enforce")

    with pytest.raises(type(fallback_error)) as raised:
        await circuit.run(
            _raising_provider("rss", calls, primary_error),
            _raising_provider("ddg", calls, fallback_error),
        )

    snapshot = circuit.snapshot()
    assert raised.value is fallback_error
    assert calls == ["rss", "ddg"]
    assert snapshot.state == "closed"
    assert snapshot.double_availability_failures == 0
    assert snapshot.open_transitions == 0


@pytest.mark.asyncio
async def test_double_failure_opens_next_generation_until_cooldown() -> None:
    clock = _Clock(10.0)
    events: list[GenericSearchCircuitEvent] = []
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=120.0,
        clock=clock,
        event_sink=events.append,
    )

    await _open_circuit(circuit)

    snapshot = circuit.snapshot()
    assert snapshot.state == "open"
    assert snapshot.generation == 1
    assert snapshot.open_until == 130.0
    assert snapshot.open_transitions == 1
    assert [event.kind for event in events] == ["open"]


@pytest.mark.asyncio
async def test_enforce_cooldown_fails_fast_without_calling_providers() -> None:
    clock = _Clock(10.0)
    events: list[GenericSearchCircuitEvent] = []
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=120.0,
        clock=clock,
        event_sink=events.append,
    )
    await _open_circuit(circuit)
    calls: list[str] = []

    with pytest.raises(GenericSearchUnavailable):
        await circuit.run(
            _successful_provider("rss", calls, object()),
            _successful_provider("ddg", calls, object()),
        )

    snapshot = circuit.snapshot()
    assert calls == []
    assert snapshot.blocked_calls == 1
    assert snapshot.state == "open"
    assert [event.kind for event in events] == ["open", "blocked"]
    assert events[-1].remaining_cooldown_seconds == 120.0


@pytest.mark.asyncio
async def test_cooldown_expiry_admits_exactly_one_enforce_probe() -> None:
    clock = _Clock(0.0)
    events: list[GenericSearchCircuitEvent] = []
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=120.0,
        clock=clock,
        event_sink=events.append,
    )
    await _open_circuit(circuit)
    clock.now = 120.0
    owner_calls: list[str] = []
    owner_started = asyncio.Event()
    owner_result = object()
    owner_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    owner = asyncio.create_task(
        circuit.run(
            _controlled_provider(
                "owner-rss",
                owner_calls,
                owner_future,
                owner_started,
            ),
            _successful_provider("owner-ddg", owner_calls, object()),
        )
    )
    await owner_started.wait()
    follower_calls: list[str] = []

    with pytest.raises(GenericSearchUnavailable):
        await circuit.run(
            _successful_provider("follower-rss", follower_calls, object()),
            _successful_provider("follower-ddg", follower_calls, object()),
        )

    assert follower_calls == []
    assert circuit.snapshot().state == "half_open"
    owner_future.set_result(owner_result)
    assert await owner is owner_result
    snapshot = circuit.snapshot()
    assert snapshot.state == "closed"
    assert snapshot.generation == 1
    assert snapshot.blocked_calls == 1
    assert snapshot.probe_successes == 1
    assert [event.kind for event in events].count("half_open") == 1


@pytest.mark.asyncio
async def test_failed_half_open_probe_opens_fresh_generation_and_cooldown() -> None:
    clock = _Clock(0.0)
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=120.0,
        clock=clock,
    )
    await _open_circuit(circuit)
    clock.now = 120.0

    await _open_circuit(circuit)

    snapshot = circuit.snapshot()
    assert snapshot.state == "open"
    assert snapshot.generation == 2
    assert snapshot.open_until == 240.0
    assert snapshot.open_transitions == 2
    assert snapshot.probe_failures == 1


@pytest.mark.asyncio
async def test_mixed_half_open_owner_failure_closes_and_propagates() -> None:
    clock = _Clock(0.0)
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=120.0,
        clock=clock,
    )
    await _open_circuit(circuit)
    clock.now = 120.0
    fallback_error = ValueError("ddg parser failed")

    with pytest.raises(ValueError) as raised:
        await circuit.run(
            _raising_provider("rss", [], TimeoutError("rss unavailable")),
            _raising_provider("ddg", [], fallback_error),
        )

    snapshot = circuit.snapshot()
    assert raised.value is fallback_error
    assert snapshot.state == "closed"
    assert snapshot.generation == 1
    assert snapshot.probe_failures == 1


@pytest.mark.asyncio
async def test_cancelled_half_open_owner_releases_generation_for_immediate_probe() -> None:
    clock = _Clock(0.0)
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=120.0,
        clock=clock,
    )
    await _open_circuit(circuit)
    clock.now = 120.0
    owner_started = asyncio.Event()
    owner_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    owner = asyncio.create_task(
        circuit.run(
            _controlled_provider("rss", [], owner_future, owner_started),
            _successful_provider("ddg", [], object()),
        )
    )
    await owner_started.wait()

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    cancelled = circuit.snapshot()
    assert cancelled.state == "open"
    assert cancelled.generation == 1
    assert cancelled.open_until == 120.0
    next_result = object()
    assert await circuit.run(
        _successful_provider("rss", [], next_result),
        _successful_provider("ddg", [], object()),
    ) is next_result
    assert circuit.snapshot().state == "closed"


@pytest.mark.asyncio
async def test_base_exception_from_half_open_owner_cannot_orphan_state() -> None:
    class FatalProviderError(BaseException):
        pass

    clock = _Clock(0.0)
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=120.0,
        clock=clock,
    )
    await _open_circuit(circuit)
    clock.now = 120.0
    fatal_error = FatalProviderError("fatal provider failure")

    with pytest.raises(FatalProviderError) as raised:
        await circuit.run(
            _raising_provider("rss", [], fatal_error),
            _successful_provider("ddg", [], object()),
        )

    snapshot = circuit.snapshot()
    assert raised.value is fatal_error
    assert snapshot.state == "closed"
    assert snapshot.probe_failures == 1


@pytest.mark.asyncio
async def test_stale_success_cannot_close_newer_open_generation() -> None:
    clock = _Clock(0.0)
    circuit = GenericSearchCircuit(mode="enforce", clock=clock)
    old_started = asyncio.Event()
    old_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    old_attempt = asyncio.create_task(
        circuit.run(
            _controlled_provider("old-rss", [], old_future, old_started),
            _successful_provider("old-ddg", [], object()),
        )
    )
    await old_started.wait()
    await _open_circuit(circuit)

    old_result = object()
    old_future.set_result(old_result)
    assert await old_attempt is old_result

    snapshot = circuit.snapshot()
    assert snapshot.state == "open"
    assert snapshot.generation == 1
    assert snapshot.open_until == 120.0


@pytest.mark.asyncio
async def test_stale_double_failure_cannot_mutate_newer_generation() -> None:
    clock = _Clock(0.0)
    events: list[GenericSearchCircuitEvent] = []
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=120.0,
        clock=clock,
        event_sink=events.append,
    )
    old_primary_started = asyncio.Event()
    old_primary: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    old_fallback_started = asyncio.Event()
    old_fallback: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    old_attempt = asyncio.create_task(
        circuit.run(
            _controlled_provider(
                "old-rss",
                [],
                old_primary,
                old_primary_started,
            ),
            _controlled_provider(
                "old-ddg",
                [],
                old_fallback,
                old_fallback_started,
            ),
        )
    )
    await old_primary_started.wait()
    new_primary = TimeoutError("new rss unavailable")
    new_fallback = ConnectionError("new ddg unavailable")
    await _open_circuit(
        circuit,
        primary_error=new_primary,
        fallback_error=new_fallback,
    )
    expected = circuit.snapshot()

    old_primary.set_exception(TimeoutError("old rss unavailable"))
    await old_fallback_started.wait()
    clock.now = 50.0
    old_fallback.set_exception(ConnectionError("old ddg unavailable"))
    with pytest.raises(GenericSearchUnavailable):
        await old_attempt

    actual = circuit.snapshot()
    assert actual.generation == expected.generation
    assert actual.open_until == expected.open_until
    assert actual.last_failure_classes == expected.last_failure_classes
    assert actual.open_transitions == expected.open_transitions
    assert [event.kind for event in events].count("open") == 1


@pytest.mark.asyncio
async def test_shadow_has_one_logical_probe_owner_while_followers_keep_calling() -> None:
    clock = _Clock(0.0)
    events: list[GenericSearchCircuitEvent] = []
    circuit = GenericSearchCircuit(
        mode="shadow",
        cooldown_seconds=120.0,
        clock=clock,
        event_sink=events.append,
    )
    await _open_circuit(circuit)
    cooldown_calls: list[str] = []
    cooldown_result = object()
    assert await circuit.run(
        _raising_provider("cooldown-rss", cooldown_calls, TimeoutError()),
        _successful_provider("cooldown-ddg", cooldown_calls, cooldown_result),
    ) is cooldown_result
    assert cooldown_calls == ["cooldown-rss", "cooldown-ddg"]

    clock.now = 120.0
    owner_started = asyncio.Event()
    owner_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    owner_result = object()
    owner = asyncio.create_task(
        circuit.run(
            _controlled_provider("owner-rss", [], owner_future, owner_started),
            _successful_provider("owner-ddg", [], object()),
        )
    )
    await owner_started.wait()
    follower_calls: list[str] = []
    with pytest.raises(GenericSearchUnavailable):
        await circuit.run(
            _raising_provider(
                "follower-rss",
                follower_calls,
                TimeoutError("rss unavailable"),
            ),
            _raising_provider(
                "follower-ddg",
                follower_calls,
                ConnectionError("ddg unavailable"),
            ),
        )

    logical = circuit.snapshot()
    assert follower_calls == ["follower-rss", "follower-ddg"]
    assert logical.state == "half_open"
    assert logical.generation == 1
    owner_future.set_result(owner_result)
    assert await owner is owner_result
    snapshot = circuit.snapshot()
    assert snapshot.state == "closed"
    assert snapshot.would_block_calls == 2
    assert snapshot.probe_successes == 1
    event_kinds = [event.kind for event in events]
    assert event_kinds.count("would_open") == 1
    assert event_kinds.count("half_open") == 1
    assert event_kinds.count("would_block") == 2


@pytest.mark.asyncio
async def test_successful_shadow_follower_cannot_close_pending_owner_generation() -> None:
    clock = _Clock(0.0)
    circuit = GenericSearchCircuit(
        mode="shadow",
        cooldown_seconds=120.0,
        clock=clock,
    )
    await _open_circuit(circuit)
    clock.now = 120.0
    owner_started = asyncio.Event()
    owner_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    owner_result = object()
    owner = asyncio.create_task(
        circuit.run(
            _controlled_provider("owner-rss", [], owner_future, owner_started),
            _successful_provider("owner-ddg", [], object()),
        )
    )
    await owner_started.wait()
    follower_calls: list[str] = []
    follower_result = object()

    assert await circuit.run(
        _successful_provider("follower-rss", follower_calls, follower_result),
        _successful_provider("follower-ddg", follower_calls, object()),
    ) is follower_result

    pending = circuit.snapshot()
    assert follower_calls == ["follower-rss"]
    assert pending.state == "half_open"
    assert pending.generation == 1
    assert pending.probe_successes == 0
    assert pending.would_block_calls == 1
    owner_future.set_result(owner_result)
    assert await owner is owner_result
    closed = circuit.snapshot()
    assert closed.state == "closed"
    assert closed.generation == 1
    assert closed.probe_successes == 1


def test_http_error_status_precedes_generic_url_error_reason_handling() -> None:
    assert isinstance(_http_error(404), urllib.error.URLError)
    assert is_provider_availability_failure(_http_error(404)) is False
    assert is_provider_availability_failure(_http_error(503)) is True
    assert is_provider_availability_failure(
        urllib.error.URLError(ConnectionRefusedError("refused"))
    ) is True
    assert is_provider_availability_failure(
        urllib.error.URLError(ValueError("bad response"))
    ) is False


@pytest.mark.asyncio
async def test_throwing_event_sink_cannot_change_results_or_circuit_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "event sink secret"

    def throwing_sink(event: GenericSearchCircuitEvent) -> None:
        raise RuntimeError(secret)

    clock = _Clock(0.0)
    circuit = GenericSearchCircuit(
        mode="enforce",
        cooldown_seconds=120.0,
        clock=clock,
        event_sink=throwing_sink,
    )

    await _open_circuit(circuit)
    opened = circuit.snapshot()
    assert opened.state == "open"
    assert opened.generation == 1
    clock.now = 120.0
    result = object()
    assert await circuit.run(
        _successful_provider("rss", [], result),
        _successful_provider("ddg", [], object()),
    ) is result
    assert circuit.snapshot().state == "closed"
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_cancelled_error_from_event_sink_is_isolated() -> None:
    def cancelled_sink(event: GenericSearchCircuitEvent) -> None:
        raise asyncio.CancelledError

    circuit = GenericSearchCircuit(mode="enforce", event_sink=cancelled_sink)

    await _open_circuit(circuit)

    snapshot = circuit.snapshot()
    assert snapshot.state == "open"
    assert snapshot.generation == 1
