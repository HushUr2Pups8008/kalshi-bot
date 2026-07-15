from __future__ import annotations

import asyncio
import ipaddress
import math
import socket
import urllib.error
import urllib.parse
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

import aiohttp
import dns.asyncresolver
import dns.exception
import dns.resolver


class AsyncAdmissionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[None]: ...


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
) -> bytes:
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

    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        admission = (
            admission_factory() if admission_factory is not None else _noop_admission()
        )
        async with asyncio.timeout_at(deadline):
            async with admission:
                addresses = await _resolve_provider_ipv4(
                    canonical_host=canonical_host,
                    provider_name=provider_name,
                    deadline=deadline,
                    resolver_factory=resolver_factory,
                )
                remaining = _remaining_https_budget(
                    deadline,
                    provider_name=provider_name,
                )
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
                        if not 200 <= response.status < 300:
                            raise urllib.error.HTTPError(
                                url,
                                response.status,
                                response.reason,
                                response.headers,
                                None,
                            )
                        try:
                            raw = await response.content.readexactly(max_bytes + 1)
                        except asyncio.IncompleteReadError as exc:
                            return exc.partial
                        raise ValueError(
                            f"{provider_name} exceeded its {max_bytes}-byte limit"
                        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        raise
    except (
        aiohttp.ClientConnectorCertificateError,
        aiohttp.ClientSSLError,
    ):
        raise
    except aiohttp.ClientConnectionError as exc:
        raise ConnectionError(f"{provider_name} connection failed") from exc
