from __future__ import annotations

import asyncio
import ipaddress
import math
import queue
import socket
import threading
import urllib.error
import urllib.parse
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
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


@dataclass(frozen=True)
class _PinnedProviderAddress:
    address: str
    family: socket.AddressFamily


def _validated_global_ip_addresses(
    values: Iterable[object],
    *,
    provider_name: str,
    expected_family: socket.AddressFamily,
) -> tuple[_PinnedProviderAddress, ...]:
    expected_type: type[ipaddress.IPv4Address] | type[ipaddress.IPv6Address]
    if expected_family == socket.AF_INET:
        expected_type = ipaddress.IPv4Address
    elif expected_family == socket.AF_INET6:
        expected_type = ipaddress.IPv6Address
    else:
        raise ValueError("expected address family is invalid")

    validated: list[_PinnedProviderAddress] = []
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
            not isinstance(resolved_ip, expected_type)
            or not resolved_ip.is_global
            or resolved_ip.is_private
            or resolved_ip.is_loopback
            or resolved_ip.is_link_local
            or resolved_ip.is_multicast
            or resolved_ip.is_unspecified
            or resolved_ip.is_reserved
            or (
                isinstance(resolved_ip, ipaddress.IPv6Address)
                and resolved_ip.ipv4_mapped is not None
            )
        ):
            raise ValueError(
                f"{provider_name} DNS answer is not a global {expected_type.__name__} address"
            )
        candidate = _PinnedProviderAddress(
            address=str(resolved_ip),
            family=expected_family,
        )
        if candidate not in validated:
            validated.append(candidate)
    return tuple(validated)


async def _resolve_provider_dual_stack(
    *,
    canonical_host: str,
    provider_name: str,
    deadline: float,
    resolver_factory: Callable[[], Any] | None = None,
) -> tuple[_PinnedProviderAddress, ...]:
    resolver = (
        resolver_factory()
        if resolver_factory is not None
        else dns.asyncresolver.Resolver()
    )

    async def resolve_family(
        record_type: str,
        family: socket.AddressFamily,
    ) -> tuple[_PinnedProviderAddress, ...]:
        try:
            answer = await resolver.resolve(
                canonical_host,
                record_type,
                lifetime=_remaining_https_budget(
                    deadline,
                    provider_name=provider_name,
                ),
                search=False,
            )
        except dns.exception.Timeout as exc:
            raise TimeoutError(f"{provider_name} DNS resolution timed out") from exc
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return ()
        except dns.resolver.NoNameservers as exc:
            raise socket.gaierror(
                socket.EAI_AGAIN,
                f"{provider_name} DNS resolution was unavailable",
            ) from exc
        return _validated_global_ip_addresses(
            (getattr(item, "address", None) for item in answer),
            provider_name=provider_name,
            expected_family=family,
        )

    resolver_tasks = (
        asyncio.create_task(resolve_family("A", socket.AF_INET)),
        asyncio.create_task(resolve_family("AAAA", socket.AF_INET6)),
    )
    try:
        ipv4_addresses, ipv6_addresses = await asyncio.gather(*resolver_tasks)
    finally:
        pending = [task for task in resolver_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    addresses = (*ipv6_addresses, *ipv4_addresses)
    if not addresses:
        raise socket.gaierror(
            socket.EAI_AGAIN,
            f"{provider_name} DNS returned no global IP addresses",
        )
    return addresses


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


class _PinnedProviderDualStackResolver(aiohttp.abc.AbstractResolver):
    def __init__(
        self,
        *,
        canonical_host: str,
        provider_name: str,
        addresses: tuple[_PinnedProviderAddress, ...],
    ) -> None:
        self._canonical_host = canonical_host
        self._provider_name = provider_name
        self._addresses = addresses

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ) -> list[dict[str, Any]]:
        if (
            host != self._canonical_host
            or port != 443
            or family not in {socket.AF_UNSPEC, socket.AF_INET, socket.AF_INET6}
        ):
            raise socket.gaierror(
                socket.EAI_NONAME,
                f"{self._provider_name} pinned resolver requires the canonical host",
            )
        return [
            {
                "hostname": self._canonical_host,
                "host": address.address,
                "port": 443,
                "family": address.family,
                "proto": socket.IPPROTO_TCP,
                "flags": socket.AI_NUMERICHOST,
            }
            for address in self._addresses
            if family == socket.AF_UNSPEC or address.family == family
        ]

    async def close(self) -> None:
        return None


@asynccontextmanager
async def _noop_admission() -> AsyncIterator[None]:
    yield


def _validated_request_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    validated: dict[str, str] = {}
    for name, value in headers.items():
        normalized = name.strip().lower() if isinstance(name, str) else ""
        if (
            not normalized
            or normalized in {"host", "user-agent"}
            or not isinstance(value, str)
            or any(char in name or char in value for char in ("\r", "\n", "\x00"))
        ):
            raise ValueError("request headers are invalid")
        validated[name] = value
    return validated


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


_SAME_HOST_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _validated_same_host_https_redirect(
    current_url: str,
    location: object,
    *,
    canonical_host: str,
    provider_name: str,
) -> str:
    if (
        not isinstance(location, str)
        or not location
        or any(char in location for char in ("\r", "\n", "\x00"))
    ):
        raise ValueError(f"{provider_name} redirect requires same-host HTTPS")

    redirect_url = urllib.parse.urljoin(current_url, location)
    try:
        parsed = urllib.parse.urlsplit(redirect_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            f"{provider_name} redirect requires same-host HTTPS"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != canonical_host
        or _is_ip_literal(parsed.hostname or "")
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{provider_name} redirect requires same-host HTTPS")
    return redirect_url


async def _fetch_bounded_https(
    url: str,
    *,
    canonical_host: str,
    provider_name: str,
    user_agent: str,
    request_headers: Mapping[str, str] | None = None,
    timeout: float,
    max_bytes: int,
    max_same_host_redirects: int = 0,
    admission_factory: AsyncAdmissionFactory | None = None,
    resolver_factory: Callable[[], Any] | None = None,
    connector_factory: Callable[..., Any] = aiohttp.TCPConnector,
    session_factory: Callable[..., Any] = aiohttp.ClientSession,
    telemetry_sink: TelemetrySink | None = None,
    address_resolver: Callable[..., Any],
    pinned_resolver_factory: Callable[[Any], aiohttp.abc.AbstractResolver],
    connector_options: Mapping[str, Any],
) -> bytes:
    """Fetch a bounded HTTPS response pinned to verified provider addresses.

    ``telemetry_sink`` is best-effort: it runs on a daemon worker thread, must
    be thread-safe, can be dropped when the bounded queue is full, and never
    affects the transport result.
    """
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != canonical_host
        or _is_ip_literal(canonical_host)
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(
            f"{provider_name} transport requires the canonical HTTPS host"
        )
    if not math.isfinite(timeout) or timeout <= 0 or max_bytes <= 0:
        raise ValueError(f"{provider_name} transport bounds must be positive")
    if (
        not isinstance(max_same_host_redirects, int)
        or isinstance(max_same_host_redirects, bool)
        or max_same_host_redirects not in {0, 1}
    ):
        raise ValueError(
            f"{provider_name} max_same_host_redirects must be zero or one"
        )
    extra_headers = _validated_request_headers(request_headers)

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
                    addresses = await address_resolver(
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
                        resolver=pinned_resolver_factory(addresses),
                        **connector_options,
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
                        headers={"User-Agent": user_agent, **extra_headers},
                    ) as session:
                        current_url = url
                        redirects_remaining = max_same_host_redirects
                        while True:
                            http_status = None
                            async with session.get(
                                current_url,
                                allow_redirects=False,
                            ) as response:
                                response_headers_ms = _elapsed_ms(
                                    response_headers_started_at,
                                    loop.time(),
                                )
                                http_status = response.status
                                location = response.headers.get("Location")
                                if (
                                    response.status in _SAME_HOST_REDIRECT_STATUSES
                                    and redirects_remaining > 0
                                    and location is not None
                                ):
                                    current_url = _validated_same_host_https_redirect(
                                        current_url,
                                        location,
                                        canonical_host=canonical_host,
                                        provider_name=provider_name,
                                    )
                                    redirects_remaining -= 1
                                    continue
                                if not 200 <= response.status < 300:
                                    raise urllib.error.HTTPError(
                                        current_url,
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
                                break
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


async def fetch_bounded_https_ipv4(
    url: str,
    *,
    canonical_host: str,
    provider_name: str,
    user_agent: str,
    request_headers: Mapping[str, str] | None = None,
    timeout: float,
    max_bytes: int,
    max_same_host_redirects: int = 0,
    admission_factory: AsyncAdmissionFactory | None = None,
    resolver_factory: Callable[[], Any] | None = None,
    connector_factory: Callable[..., Any] = aiohttp.TCPConnector,
    session_factory: Callable[..., Any] = aiohttp.ClientSession,
    telemetry_sink: TelemetrySink | None = None,
) -> bytes:
    """Fetch a bounded HTTPS response pinned to verified IPv4 addresses."""
    return await _fetch_bounded_https(
        url,
        canonical_host=canonical_host,
        provider_name=provider_name,
        user_agent=user_agent,
        request_headers=request_headers,
        timeout=timeout,
        max_bytes=max_bytes,
        max_same_host_redirects=max_same_host_redirects,
        admission_factory=admission_factory,
        resolver_factory=resolver_factory,
        connector_factory=connector_factory,
        session_factory=session_factory,
        telemetry_sink=telemetry_sink,
        address_resolver=_resolve_provider_ipv4,
        pinned_resolver_factory=lambda addresses: _PinnedProviderIPv4Resolver(
            canonical_host=canonical_host,
            provider_name=provider_name,
            addresses=addresses,
        ),
        connector_options={
            "family": socket.AF_INET,
            "use_dns_cache": False,
            "force_close": True,
            "limit": 1,
            "limit_per_host": 1,
            "happy_eyeballs_delay": None,
        },
    )


async def fetch_bounded_https_dual_stack(
    url: str,
    *,
    canonical_host: str,
    provider_name: str,
    user_agent: str,
    request_headers: Mapping[str, str] | None = None,
    timeout: float,
    max_bytes: int,
    admission_factory: AsyncAdmissionFactory | None = None,
    resolver_factory: Callable[[], Any] | None = None,
    connector_factory: Callable[..., Any] = aiohttp.TCPConnector,
    session_factory: Callable[..., Any] = aiohttp.ClientSession,
    telemetry_sink: TelemetrySink | None = None,
) -> bytes:
    """Fetch a bounded HTTPS response pinned to verified IPv4 and IPv6 addresses."""
    return await _fetch_bounded_https(
        url,
        canonical_host=canonical_host,
        provider_name=provider_name,
        user_agent=user_agent,
        request_headers=request_headers,
        timeout=timeout,
        max_bytes=max_bytes,
        admission_factory=admission_factory,
        resolver_factory=resolver_factory,
        connector_factory=connector_factory,
        session_factory=session_factory,
        telemetry_sink=telemetry_sink,
        address_resolver=_resolve_provider_dual_stack,
        pinned_resolver_factory=lambda addresses: _PinnedProviderDualStackResolver(
            canonical_host=canonical_host,
            provider_name=provider_name,
            addresses=addresses,
        ),
        connector_options={
            "family": socket.AF_UNSPEC,
            "use_dns_cache": False,
            "force_close": True,
            "limit": 1,
            "limit_per_host": 1,
            "happy_eyeballs_delay": 0.25,
            "interleave": 1,
        },
    )
