from __future__ import annotations

import asyncio
import ipaddress
import math
import queue
import socket
import threading
import urllib.error
import urllib.parse
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp
import dns.asyncresolver
import dns.exception
import dns.resolver


class AsyncAdmissionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[None]: ...


@dataclass(frozen=True)
class BoundedHTTPSAttemptTelemetry:
    provider_name: str
    outcome: str
    terminal_stage: str
    budget_ms: int
    total_ms: int
    admission_wait_ms: int
    dns_ms: int | None
    response_headers_ms: int | None
    body_read_ms: int | None
    http_status: int | None
    bytes_read: int | None
    error_class: str | None


# Best effort only: callbacks run on a daemon worker, must be thread-safe, can
# be dropped when the queue is full, and never change the transport outcome.
TelemetrySink = Callable[[BoundedHTTPSAttemptTelemetry], None]


@dataclass(frozen=True)
class _TelemetryDispatchStats:
    accepted: int
    dropped: int
    worker_alive: bool
    worker_daemon: bool


class _TelemetryDispatcher:
    def __init__(self, *, capacity: int = 64) -> None:
        if capacity <= 0:
            raise ValueError("telemetry dispatcher capacity must be positive")
        self._queue: queue.Queue[tuple[TelemetrySink, BoundedHTTPSAttemptTelemetry]] = (
            queue.Queue(maxsize=capacity)
        )
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._accepted = 0
        self._dropped = 0

    def submit(
        self,
        sink: TelemetrySink,
        event: BoundedHTTPSAttemptTelemetry,
    ) -> bool:
        with self._lock:
            worker = self._worker
            if worker is None or not worker.is_alive():
                worker = threading.Thread(
                    target=self._run,
                    name="bounded-https-telemetry",
                    daemon=True,
                )
                try:
                    worker.start()
                except RuntimeError:
                    self._dropped += 1
                    return False
                self._worker = worker
            try:
                self._queue.put_nowait((sink, event))
            except queue.Full:
                self._dropped += 1
                return False
            self._accepted += 1
            return True

    def snapshot(self) -> _TelemetryDispatchStats:
        with self._lock:
            worker = self._worker
            return _TelemetryDispatchStats(
                accepted=self._accepted,
                dropped=self._dropped,
                worker_alive=worker is not None and worker.is_alive(),
                worker_daemon=worker is not None and worker.daemon,
            )

    def _run(self) -> None:
        while True:
            sink, event = self._queue.get()
            try:
                sink(event)
            except BaseException:
                pass
            finally:
                self._queue.task_done()


_TELEMETRY_DISPATCHER = _TelemetryDispatcher()


def _elapsed_ms(started_at: float, ended_at: float) -> int:
    return max(0, round((ended_at - started_at) * 1000))


def _emit_telemetry(
    telemetry_sink: TelemetrySink | None,
    event: BoundedHTTPSAttemptTelemetry,
) -> None:
    if telemetry_sink is None:
        return
    _TELEMETRY_DISPATCHER.submit(telemetry_sink, event)


def _validated_global_ipv4_addresses(
    values: Iterable[object], *, provider_name: str
) -> tuple[str, ...]:
    validated: list[str] = []
    for address in values:
        if not isinstance(address, str):
            raise ValueError(f"{provider_name} DNS answer has an invalid IP address")
        try:
            resolved_ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError(
                f"{provider_name} DNS answer has an invalid IP address"
            ) from exc
        if (
            not isinstance(resolved_ip, ipaddress.IPv4Address)
            or not resolved_ip.is_global
            or resolved_ip.is_private
            or resolved_ip.is_loopback
            or resolved_ip.is_link_local
            or resolved_ip.is_multicast
            or resolved_ip.is_unspecified
            or resolved_ip.is_reserved
        ):
            raise ValueError(
                f"{provider_name} DNS answer is not a global IPv4 address"
            )
        normalized = str(resolved_ip)
        if normalized not in validated:
            validated.append(normalized)
    if not validated:
        raise socket.gaierror(
            socket.EAI_AGAIN,
            f"{provider_name} DNS returned no IPv4 addresses",
        )
    return tuple(validated)


def _remaining_https_budget(deadline: float, *, provider_name: str) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError(f"{provider_name} exceeded its total timeout")
    return remaining


async def _resolve_provider_ipv4(
    *,
    canonical_host: str,
    provider_name: str,
    deadline: float,
    resolver_factory: Callable[[], Any] | None = None,
) -> tuple[str, ...]:
    resolver = (
        resolver_factory()
        if resolver_factory is not None
        else dns.asyncresolver.Resolver()
    )
    try:
        answer = await resolver.resolve(
            canonical_host,
            "A",
            lifetime=_remaining_https_budget(
                deadline,
                provider_name=provider_name,
            ),
            search=False,
        )
    except dns.exception.Timeout as exc:
        raise TimeoutError(f"{provider_name} DNS resolution timed out") from exc
    except (
        dns.resolver.NXDOMAIN,
        dns.resolver.NoAnswer,
        dns.resolver.NoNameservers,
    ) as exc:
        raise socket.gaierror(
            socket.EAI_AGAIN,
            f"{provider_name} DNS resolution was unavailable",
        ) from exc
    return _validated_global_ipv4_addresses(
        (getattr(item, "address", None) for item in answer),
        provider_name=provider_name,
    )


class _PinnedProviderIPv4Resolver(aiohttp.abc.AbstractResolver):
    def __init__(
        self,
        *,
        canonical_host: str,
        provider_name: str,
        addresses: tuple[str, ...],
    ) -> None:
        self._canonical_host = canonical_host
        self._provider_name = provider_name
        self._addresses = addresses

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[dict[str, Any]]:
        if (
            host != self._canonical_host
            or port != 443
            or family != socket.AF_INET
        ):
            raise socket.gaierror(
                socket.EAI_NONAME,
                f"{self._provider_name} pinned resolver requires the canonical host",
            )
        return [
            {
                "hostname": self._canonical_host,
                "host": address,
                "port": 443,
                "family": socket.AF_INET,
                "proto": socket.IPPROTO_TCP,
                "flags": socket.AI_NUMERICHOST,
            }
            for address in self._addresses
        ]

    async def close(self) -> None:
        return None


@asynccontextmanager
async def _noop_admission() -> AsyncIterator[None]:
    yield


async def fetch_bounded_https_ipv4(
    url: str,
    *,
    canonical_host: str,
    provider_name: str,
    user_agent: str,
    timeout: float,
    max_bytes: int,
    admission_factory: AsyncAdmissionFactory | None = None,
    resolver_factory: Callable[[], Any] | None = None,
    connector_factory: Callable[..., Any] = aiohttp.TCPConnector,
    session_factory: Callable[..., Any] = aiohttp.ClientSession,
    telemetry_sink: TelemetrySink | None = None,
) -> bytes:
    """Fetch a bounded HTTPS response pinned to verified IPv4 addresses.

    ``telemetry_sink`` is best-effort: it runs on a daemon worker thread, must
    be thread-safe, can be dropped when the bounded queue is full, and never
    affects the transport result.
    """
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != canonical_host
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(
            f"{provider_name} transport requires the canonical HTTPS host"
        )
    if not math.isfinite(timeout) or timeout <= 0 or max_bytes <= 0:
        raise ValueError(f"{provider_name} transport bounds must be positive")

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    deadline = started_at + timeout
    terminal_stage = "admission"
    outcome = "error"
    error_class: str | None = None
    admission_wait_ms: int | None = None
    dns_ms: int | None = None
    response_headers_ms: int | None = None
    body_read_ms: int | None = None
    http_status: int | None = None
    bytes_read: int | None = None

    try:
        admission = (
            admission_factory() if admission_factory is not None else _noop_admission()
        )
        async with asyncio.timeout_at(deadline):
            admission_started_at = loop.time()
            async with admission:
                admission_wait_ms = _elapsed_ms(admission_started_at, loop.time())
                terminal_stage = "dns"
                dns_started_at = loop.time()
                try:
                    addresses = await _resolve_provider_ipv4(
                        canonical_host=canonical_host,
                        provider_name=provider_name,
                        deadline=deadline,
                        resolver_factory=resolver_factory,
                    )
                finally:
                    dns_ms = _elapsed_ms(dns_started_at, loop.time())
                remaining = _remaining_https_budget(
                    deadline,
                    provider_name=provider_name,
                )
                terminal_stage = "response_headers"
                response_headers_started_at = loop.time()
                try:
                    connector = connector_factory(
                        resolver=_PinnedProviderIPv4Resolver(
                            canonical_host=canonical_host,
                            provider_name=provider_name,
                            addresses=addresses,
                        ),
                        family=socket.AF_INET,
                        use_dns_cache=False,
                        force_close=True,
                        limit=1,
                        limit_per_host=1,
                        happy_eyeballs_delay=None,
                    )
                    client_timeout = aiohttp.ClientTimeout(
                        total=remaining,
                        connect=remaining,
                        sock_connect=remaining,
                        sock_read=remaining,
                    )
                    async with session_factory(
                        connector=connector,
                        timeout=client_timeout,
                        headers={"User-Agent": user_agent},
                    ) as session:
                        async with session.get(url, allow_redirects=False) as response:
                            response_headers_ms = _elapsed_ms(
                                response_headers_started_at,
                                loop.time(),
                            )
                            http_status = response.status
                            if not 200 <= response.status < 300:
                                raise urllib.error.HTTPError(
                                    url,
                                    response.status,
                                    response.reason,
                                    response.headers,
                                    None,
                                )
                            terminal_stage = "body_read"
                            body_read_started_at = loop.time()
                            try:
                                raw = await response.content.readexactly(max_bytes + 1)
                            except asyncio.IncompleteReadError as exc:
                                raw = exc.partial
                            finally:
                                body_read_ms = _elapsed_ms(
                                    body_read_started_at,
                                    loop.time(),
                                )
                            bytes_read = len(raw)
                            if len(raw) > max_bytes:
                                raise ValueError(
                                    f"{provider_name} exceeded its {max_bytes}-byte limit"
                                )
                finally:
                    if response_headers_ms is None:
                        response_headers_ms = _elapsed_ms(
                            response_headers_started_at,
                            loop.time(),
                        )
            outcome = "success"
            terminal_stage = "complete"
            return raw
    except asyncio.CancelledError:
        outcome = "cancelled"
        error_class = "CancelledError"
        raise
    except TimeoutError:
        outcome = "timeout"
        error_class = "TimeoutError"
        raise
    except (
        aiohttp.ClientConnectorCertificateError,
        aiohttp.ClientSSLError,
    ) as exc:
        outcome = "error"
        error_class = type(exc).__name__
        raise
    except aiohttp.ClientConnectionError as exc:
        outcome = "error"
        error_class = type(exc).__name__
        raise ConnectionError(f"{provider_name} connection failed") from exc
    except Exception as exc:
        outcome = "error"
        error_class = type(exc).__name__
        raise
    finally:
        if admission_wait_ms is None:
            admission_wait_ms = _elapsed_ms(started_at, loop.time())
        _emit_telemetry(
            telemetry_sink,
            BoundedHTTPSAttemptTelemetry(
                provider_name=provider_name,
                outcome=outcome,
                terminal_stage=terminal_stage,
                budget_ms=_elapsed_ms(started_at, deadline),
                total_ms=_elapsed_ms(started_at, loop.time()),
                admission_wait_ms=admission_wait_ms,
                dns_ms=dns_ms,
                response_headers_ms=response_headers_ms,
                body_read_ms=body_read_ms,
                http_status=http_status,
                bytes_read=bytes_read,
                error_class=error_class,
            ),
        )
