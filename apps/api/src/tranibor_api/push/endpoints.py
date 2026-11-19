# SPDX-License-Identifier: Apache-2.0
"""Which push endpoints the platform agrees to call (ADR 0194 §7).

The endpoint is a URL the CLIENT chose, and the API will POST to it from inside the cluster: it is
a server-side request forgery waiting to happen unless it is checked. Only a public `https` host
passes; the host is resolved and EVERY address must be public. A deployment that really pushes to
an internal service (a self-hosted push gateway) opts in with `CHOREGOS_PUSH_ALLOW_PRIVATE_ENDPOINTS`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

MAX_ENDPOINT_BYTES = 2048


class EndpointRefusedError(ValueError):
    """The endpoint is not one the platform will call; the message says why, without the URL."""


def host_of(endpoint: str) -> str:
    """The host to show a person: where the device is registered, never the secret path."""
    return urlsplit(endpoint).hostname or ""


def origin_of(endpoint: str) -> str:
    """The push service's origin: the `aud` of the VAPID token (RFC 8292 §2)."""
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    return ip.is_global and not ip.is_multicast


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


async def check_endpoint(endpoint: str, *, allow_private: bool = False) -> None:
    """Raises `EndpointRefusedError` unless `endpoint` may be called."""
    if len(endpoint.encode()) > MAX_ENDPOINT_BYTES:
        raise EndpointRefusedError(f"the endpoint exceeds {MAX_ENDPOINT_BYTES} bytes")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError as error:
        raise EndpointRefusedError("the endpoint is not a URL") from error
    if parts.scheme != "https":
        raise EndpointRefusedError("the endpoint must be an https URL")
    if parts.username is not None or parts.password is not None:
        raise EndpointRefusedError("the endpoint must not carry credentials")
    host = parts.hostname
    if not host:
        raise EndpointRefusedError("the endpoint has no host")
    if allow_private:
        return
    if port not in (None, 443):
        raise EndpointRefusedError("the endpoint must use the https port")
    try:
        addresses = [host] if _is_ip(host) else await _resolve(host)
    except OSError as error:
        raise EndpointRefusedError("the endpoint's host does not resolve") from error
    if not addresses or not all(_public(address) for address in addresses):
        raise EndpointRefusedError("the endpoint's host is not a public address")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
