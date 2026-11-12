# SPDX-License-Identifier: Apache-2.0
"""`choregos-outpost doctor`: what stands between this machine and a working outpost.

One check each, and each says what to do when it fails:

- `policy`: the policy file is valid, `door_keys` included (each entry parses as a key);
- `identity`: the instance's keys are there and are the daemon's alone;
- `records`: the audit log and the journal can be written (the daemon does not serve without them);
- `dns`: the name the daemon dials resolves, the outbound proxy's when there is one;
- `proxy`: the outbound proxy opens a tunnel to the door;
- `tls`: the door's certificate chain verifies, behind a private certificate authority too;
- `door`: the door answers at `control_plane`, with one of its own problems;
- `clock`: this machine's clock is within what the door takes, read from the door's `server_time`;
- `endpoint <alias>`: each alias's own server answers a tool listing with its credential.

Nothing here signs a request or polls: the door is probed with a request it refuses, so that
`doctor` can run beside a daemon without taking its place (a second poll under one key is what a
stolen key looks like). Nothing prints a key or a credential.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
import ssl
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from choregos_contracts.outpost import IAT_WINDOW_S, OUTPOST_DOOR_PREFIX, OutpostDoorProblem, OutpostPolicy
from cryptography import x509
from pydantic import ValidationError

from . import identity as identities
from . import policy as policies
from . import uplink
from .audit import AuditLog, AuditWriteError
from .settings import Settings, SettingsError, without_credentials
from .toolhost import EndpointUnavailableError, ResultTooLargeError, ToolHost, TransportFactory

OK, FAILED, SKIPPED = "ok", "failed", "skipped"
_TIMEOUT_S = 10.0
Resolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAILED


def _ok(name: str, detail: str) -> Check:
    return Check(name, OK, detail)


def _failed(name: str, detail: str, hint: str = "") -> Check:
    return Check(name, FAILED, detail, hint)


def _skipped(name: str, detail: str) -> Check:
    return Check(name, SKIPPED, detail)


# ───────────────────────── the files ─────────────────────────


def policy_check(path: Path) -> tuple[Check, OutpostPolicy | None]:
    try:
        policy = policies.load(path)
    except policies.PolicyError as error:
        hint = "the daemon does not start on this file"
        if any(fault.startswith("door_keys") for fault in error.faults):
            hint += ": `door_keys` holds the door's keys as the console shows them, one to four"
        return _failed("policy", f"{path}: " + "; ".join(error.faults), hint), None
    pinned = len(policy.door_keys)
    return _ok(
        "policy", f"{path}: valid, {len(policy.endpoints)} endpoint(s), {pinned} door key(s) pinned"
    ), policy


def identity_check(directory: Path) -> Check:
    if not identities.exists(directory):
        return _skipped("identity", f"{directory}: no key yet; `choregos-outpost enrol` makes them")
    try:
        identity = identities.load(directory)
        enrolment = identities.load_enrolment(directory)
    except identities.IdentityError as error:
        return _failed("identity", str(error))
    if enrolment is None:
        return _ok("identity", f"{identity.fingerprint}, not enrolled yet")
    where = f"{enrolment.organisation}/{enrolment.outpost}"
    return _ok("identity", f"{identity.fingerprint}, instance {enrolment.instance_id} of {where}")


def _writable(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".doctor-"):
        pass


async def records_check(policy: OutpostPolicy, journal_dir: Path) -> Check:
    audit = Path(policy.audit.file)
    try:
        await AuditLog(audit).check()
        await asyncio.to_thread(_writable, journal_dir)
    except AuditWriteError as error:
        return _failed("records", str(error), "the daemon executes nothing it cannot record")
    except OSError as error:
        reason = error.strerror or str(error)
        return _failed(
            "records",
            f"{journal_dir}: the journal cannot be written ({reason})",
            "the daemon executes nothing it cannot record",
        )
    return _ok("records", f"{audit} and {journal_dir} can be written")


# ───────────────────────── the path to the door ─────────────────────────


def _target(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    return parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)


async def _resolve(host: str, port: int) -> list[str]:
    found = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(entry[4][0]) for entry in found})


async def dns_check(control_plane: str, proxy: str | None, resolve: Resolver | None = None) -> Check:
    """The name the daemon itself resolves: its proxy's when it has one, the door's otherwise."""
    host, port = _target(proxy or control_plane)
    whose = "the outbound proxy" if proxy else "the door"
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        return _skipped("dns", f"{whose} is given by its address ({host}): no name to resolve")
    try:
        addresses = await asyncio.wait_for((resolve or _resolve)(host, port), _TIMEOUT_S)
    except (OSError, TimeoutError) as error:
        reason = getattr(error, "strerror", None) or str(error) or "no answer"
        return _failed(
            "dns",
            f"{host} ({whose}) does not resolve: {reason}",
            "check the resolver this machine uses, and the name",
        )
    return _ok("dns", f"{host} ({whose}) resolves to {', '.join(addresses)}")


class _TunnelRefusedError(Exception):
    """The proxy answered something other than a tunnel."""


async def _tunnel(proxy: str, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A TCP connection to `host:port` through an HTTP proxy (`CONNECT`)."""
    parts = urlsplit(proxy)
    proxy_host, proxy_port = _target(proxy)
    reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
    lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
    if parts.username:
        pair = f"{unquote(parts.username)}:{unquote(parts.password or '')}".encode()
        lines.append("Proxy-Authorization: Basic " + base64.b64encode(pair).decode("ascii"))
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode("latin-1")
    if status.split(" ")[1:2] != ["200"]:
        writer.close()
        raise _TunnelRefusedError(status)
    return reader, writer


async def proxy_check(control_plane: str, proxy: str | None) -> Check:
    if not proxy:
        return _skipped("proxy", "no outbound proxy is configured: the door is dialled directly")
    shown = without_credentials(proxy)
    if urlsplit(proxy).scheme != "http":
        return _skipped("proxy", f"{shown}: only an `http://` proxy is probed here")
    host, port = _target(control_plane)
    try:
        _, writer = await asyncio.wait_for(_tunnel(proxy, host, port), _TIMEOUT_S)
    except _TunnelRefusedError as refused:
        hint = "the proxy must let this machine open a tunnel to the door's hostname, port 443, and no other"
        if " 407" in str(refused):
            hint = "the proxy asks for credentials: give them in its URL (`http://user:password@host:port`)"
        return _failed("proxy", f"{shown} refused a tunnel to {host}:{port}: {refused}", hint)
    except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
        reason = getattr(error, "strerror", None) or str(error) or type(error).__name__
        return _failed("proxy", f"{shown} cannot be reached: {reason}", "check `--proxy` (or HTTPS_PROXY)")
    writer.close()
    return _ok("proxy", f"{shown} opens a tunnel to {host}:{port}")


def _issuer(der: bytes | None) -> str:
    if not der:
        return "nobody this machine could read"
    certificate = x509.load_der_x509_certificate(der)
    return certificate.issuer.rfc4514_string() or "an authority without a name"


async def _handshake(control_plane: str, proxy: str | None, context: ssl.SSLContext) -> bytes | None:
    """One TLS handshake with the door, through the proxy if any; the certificate it presented."""
    host, port = _target(control_plane)
    if proxy and urlsplit(proxy).scheme == "http":
        _, writer = await _tunnel(proxy, host, port)
        await writer.start_tls(context, server_hostname=host)
    else:
        _, writer = await asyncio.open_connection(host, port, ssl=context, server_hostname=host)
    try:
        presented: bytes | None = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        return presented
    finally:
        writer.close()


async def tls_check(control_plane: str, proxy: str | None, context: ssl.SSLContext) -> Check:
    """Whether the chain the door presents verifies against what this machine trusts. Behind a proxy
    that inspects TLS the chain is the proxy's, signed by the organisation's own authority."""
    host, _ = _target(control_plane)
    if urlsplit(control_plane).scheme != "https":
        return _skipped("tls", "the control plane is plain `http`: nothing to verify (a local bench only)")
    try:
        presented = await asyncio.wait_for(_handshake(control_plane, proxy, context), _TIMEOUT_S)
    except ssl.SSLCertVerificationError as error:
        # Looked at again without verifying, only to say who signed what was presented.
        blind = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        blind.check_hostname = False
        blind.verify_mode = ssl.CERT_NONE
        try:
            issuer = _issuer(await asyncio.wait_for(_handshake(control_plane, proxy, blind), _TIMEOUT_S))
        except (OSError, TimeoutError, ValueError, _TunnelRefusedError):
            issuer = "nobody this machine could read"
        return _failed(
            "tls",
            f"the certificate {host} presents does not verify ({error.verify_message}); "
            f"it is issued by {issuer}",
            "if an outbound proxy of the organisation inspects TLS, or the door's authority is private, give "
            "that authority's certificate with `--ca-file` (CHOREGOS_OUTPOST_CA_FILE)",
        )
    except (OSError, TimeoutError, _TunnelRefusedError, asyncio.IncompleteReadError) as error:
        reason = getattr(error, "strerror", None) or str(error) or type(error).__name__
        return _failed("tls", f"no TLS session with {host}: {reason}")
    return _ok("tls", f"the chain of {host} verifies (issued by {_issuer(presented)})")


async def door_check(
    control_plane: str, http: httpx.AsyncClient, now: Callable[[], float]
) -> tuple[Check, float | None]:
    """Asks the door a request it must refuse: an unsigned poll. A problem of the door proves the door
    is there, and carries its clock. Returns the check and the clock's drift, door minus machine."""
    url = f"{control_plane}{OUTPOST_DOOR_PREFIX}/poll"
    try:
        response = await http.post(url, content=b"{}", headers={"Content-Type": "application/json"})
        problem = OutpostDoorProblem.model_validate(json.loads(response.content))
    except httpx.HTTPError as error:
        reason = f"{type(error).__name__}: {error}".rstrip(": ")
        return _failed("door", f"{url} cannot be reached: {reason}"), None
    except (ValueError, ValidationError):
        return (
            _failed(
                "door",
                f"{url} answered HTTP {response.status_code}, which is not one of the door's problems",
                "outposts are switched off at this origin, or `control_plane` is not the door's hostname",
            ),
            None,
        )
    drift = problem.server_time.timestamp() - now()
    return _ok(
        "door", f"the door answers at {control_plane} ({problem.status} {problem.code}, as it must)"
    ), drift


def clock_check(drift: float | None) -> Check:
    if drift is None:
        return _skipped("clock", "the door did not answer: its time is unknown")
    side = "behind" if drift > 0 else "ahead of"
    if abs(drift) > IAT_WINDOW_S:
        return _failed(
            "clock",
            f"this machine's clock is {abs(drift):.0f} s {side} the door's: the door takes {IAT_WINDOW_S} s",
            "every request would be refused `stale_iat`: set the clock (NTP)",
        )
    return _ok(
        "clock",
        f"this machine's clock is {abs(drift):.0f} s from the door's (the door takes {IAT_WINDOW_S} s)",
    )


# ───────────────────────── the endpoints ─────────────────────────


async def endpoint_check(alias: str, policy: OutpostPolicy, host: ToolHost) -> Check:
    endpoint = policy.endpoints[alias]
    name = f"endpoint {alias}"
    try:
        listed = await host.listing(alias, within_s=float(endpoint.timeout_s))
    except EndpointUnavailableError as error:
        return _failed(
            name, f"its server does not answer a tool listing: {error}", "check its `url` and its `auth`"
        )
    except ResultTooLargeError:
        return _failed(name, "its tool listing is above what the outpost reads", "raise `max_payload_kb`")
    within = [tool for tool in listed if policies.allows(endpoint, tool.name)]
    read_only = sum(1 for tool in within if tool.read_only)
    return _ok(
        name,
        f"{len(listed)} tool(s) listed, {len(within)} within its patterns, {read_only} of them declared "
        f"read-only (writes: {endpoint.writes})",
    )


# ───────────────────────── all of them ─────────────────────────


async def diagnose(
    settings: Settings,
    *,
    door: httpx.AsyncBaseTransport | None = None,
    endpoints: TransportFactory | None = None,
    resolve: Resolver | None = None,
    now: Callable[[], float] = time.time,
) -> list[Check]:
    """Every check, in the order a call would meet what they look at."""
    first, policy = policy_check(settings.policy)
    checks = [first, identity_check(settings.identity_dir)]
    if policy is None:
        unknown = "no valid policy: the door and the endpoints are unknown"
        return [
            *checks,
            *(_skipped(name, unknown) for name in ("records", "dns", "proxy", "tls", "door", "clock")),
        ]
    checks.append(await records_check(policy, settings.journal_dir))
    try:
        context = settings.trust()
    except SettingsError as error:
        return [*checks, _failed("tls", str(error))]
    proxy = settings.proxy_for(policy.control_plane)
    checks.append(await dns_check(policy.control_plane, proxy, resolve))
    checks.append(await proxy_check(policy.control_plane, proxy))
    checks.append(await tls_check(policy.control_plane, proxy, context))
    async with uplink.client(transport=door, verify=context, proxy=proxy) as http:
        reached, drift = await door_check(policy.control_plane, http, now)
    checks += [reached, clock_check(drift)]
    host = ToolHost(policy, transport=endpoints, verify=context)
    checks += [await endpoint_check(alias, policy, host) for alias in sorted(policy.endpoints)]
    return checks


def render(checks: list[Check]) -> str:
    """The checks as a person reads them: one line each, and what to do under a failure."""
    width = max(len(check.name) for check in checks)
    lines: list[str] = []
    for check in checks:
        lines.append(
            f"{check.status.upper() if check.failed else check.status:<8}"
            f"{check.name:<{width + 2}}{check.detail}"
        )
        if check.failed and check.hint:
            lines.append(f"{'':<8}{'':<{width + 2}}-> {check.hint}")
    return "\n".join(lines)


def as_json(checks: list[Check]) -> str:
    return json.dumps(
        {"ok": not any(c.failed for c in checks), "checks": [asdict(c) for c in checks]}, indent=2
    )


__all__ = ["Check", "as_json", "diagnose", "render"]
