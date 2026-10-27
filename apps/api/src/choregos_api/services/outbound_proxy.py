# SPDX-License-Identifier: Apache-2.0
"""An organisation's outbound proxy: the setting, its resolution, its test (S40-26b, ADR 0087).

The setting is a key of `Organization.settings` (like the policy ceiling); its password is sealed
in the organisation's secret store (ADR 0058) under a derived name and never leaves it except to
`outbound_proxy_for`, the one resolver every client goes through. Fail closed: a proxy that is
enabled for a kind of traffic but cannot be built (no secrets key, the password lost) is an
`OutboundProxyError`, never a silent direct call.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from choregos_adapters.http.proxy import FixedProxyTransport
from choregos_core.outbound_proxy import (
    KINDS,
    OutboundProxyError,
    ProxyConfig,
    pop_proxies,
    push_proxies,
)
from choregos_core.redaction import redact, remember_secret
from choregos_core.secrets import SecretIntrouvable
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Organization
from ..schemas.outbound_proxy import OutboundProxy, OutboundProxyState, OutboundProxyTest
from ..secret_store import SecretStore, SecretStoreUnavailable, get_secret_store

SETTINGS_KEY = "outbound_proxy"
#: Where the password is sealed (ADR 0058). One per organisation, since there is one proxy.
SECRET_NAME = "org/outbound-proxy/password"  # noqa: S105 - a name in the secret store, not a secret
TEST_TARGET = "https://ipinfo.io/json"
TEST_TIMEOUT_S = 15.0


def stored(organisation: Organization | None) -> dict[str, Any] | None:
    """The setting as stored (no password: that one is in the secret store), or `None`."""
    if organisation is None:
        return None
    found = (organisation.settings or {}).get(SETTINGS_KEY)
    return found if isinstance(found, dict) and found.get("proxy") else None


async def state_of(
    session: AsyncSession, organisation: Organization, store: SecretStore | None = None
) -> OutboundProxyState:
    found = stored(organisation)
    if found is None:
        return OutboundProxyState()
    meta = await (store or get_secret_store()).meta(session, organisation.id, [SECRET_NAME])
    proxy = found["proxy"]
    return OutboundProxyState(
        proxy=OutboundProxy(
            enabled=bool(proxy["enabled"]),
            endpoint=proxy["endpoint"],
            scheme=proxy["scheme"],
            username=proxy["username"],
            password_set=SECRET_NAME in meta,
            applies_to=[k for k in KINDS if k in proxy["applies_to"]],
        ),
        updated_by=found.get("updated_by"),
        updated_at=found.get("updated_at"),
    )


async def _config(
    session: AsyncSession, organisation: Organization, proxy: dict[str, Any], store: SecretStore | None
) -> ProxyConfig:
    label = f"{proxy['scheme']}://{proxy['endpoint']}"
    try:
        values = await (store or get_secret_store()).get_many(session, organisation.id, [SECRET_NAME])
    except (SecretIntrouvable, SecretStoreUnavailable) as lost:
        raise OutboundProxyError(
            f"the outbound proxy {label} cannot be used: its password is not available ({lost})"
        ) from lost
    password = values[SECRET_NAME]
    config = ProxyConfig(proxy["endpoint"], proxy["scheme"], proxy["username"], password)
    for form in config.secrets():
        remember_secret(form)
    return config


async def outbound_proxy_for(
    session: AsyncSession, org_id: str, kind: str, store: SecretStore | None = None
) -> ProxyConfig | None:
    """The proxy the organisation's `kind` of traffic must go through, or `None` for a direct call:
    none set, disabled, or `kind` outside `applies_to`. A proxy that applies but cannot be built raises
    `OutboundProxyError`: the caller refuses the call, it never goes direct."""
    organisation = await session.get(Organization, org_id)
    found = stored(organisation)
    if organisation is None or found is None:
        return None
    proxy = found["proxy"]
    if not proxy.get("enabled") or kind not in proxy.get("applies_to", []):
        return None
    return await _config(session, organisation, proxy, store)


async def stored_config(
    session: AsyncSession, organisation: Organization, store: SecretStore | None = None
) -> ProxyConfig | None:
    """The stored proxy whatever `enabled` and `applies_to` say (the `test` action)."""
    found = stored(organisation)
    if found is None:
        return None
    return await _config(session, organisation, found["proxy"], store)


@asynccontextmanager
async def proxy_scope(
    session: AsyncSession, org_id: str, kinds: tuple[str, ...], store: SecretStore | None = None
) -> AsyncIterator[None]:
    """Calls made inside the block, by guarded clients that read the context, go through the
    organisation's proxy for each of `kinds` it applies to."""
    found: dict[str, ProxyConfig] = {}
    for kind in kinds:
        proxy = await outbound_proxy_for(session, org_id, kind, store)
        if proxy is not None:
            found[kind] = proxy
    if not found:
        yield
        return
    token = push_proxies(found)
    try:
        yield
    finally:
        pop_proxies(token)


def _scrub(text: str, proxy: ProxyConfig) -> str:
    for form in proxy.secrets():
        text = text.replace(form, "***")
    return redact(text)


async def run_test(proxy: ProxyConfig) -> OutboundProxyTest:
    """`GET https://ipinfo.io/json` through `proxy`: what the exit looks like, or why it failed. A
    failing proxy is a result (`ok: false`), not an exception; the target is fixed by the platform."""
    started = time.monotonic()

    def took() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        async with httpx.AsyncClient(
            timeout=TEST_TIMEOUT_S,
            transport=FixedProxyTransport(proxy),
            follow_redirects=False,
            headers={"Accept": "application/json"},
        ) as client:
            response = await client.get(TEST_TARGET)
        if response.status_code != 200:
            return OutboundProxyTest(
                ok=False,
                error=f"{TEST_TARGET} answered HTTP {response.status_code} through {proxy.label}",
                elapsed_ms=took(),
                target=TEST_TARGET,
            )
        body = response.json()
        ip = body.get("ip") if isinstance(body, dict) else None
        country = body.get("country") if isinstance(body, dict) else None
        if not isinstance(ip, str):
            return OutboundProxyTest(
                ok=False,
                error=f"{TEST_TARGET} answered through {proxy.label} with no `ip`",
                elapsed_ms=took(),
                target=TEST_TARGET,
            )
        return OutboundProxyTest(
            ok=True,
            exit_ip=ip,
            country=country if isinstance(country, str) else None,
            elapsed_ms=took(),
            target=TEST_TARGET,
        )
    except OutboundProxyError as failed:
        error = _scrub(str(failed), proxy)
    except (httpx.HTTPError, ValueError, OSError) as failed:
        error = _scrub(f"the outbound proxy {proxy.label} failed: {type(failed).__name__}", proxy)
    return OutboundProxyTest(ok=False, error=error, elapsed_ms=took(), target=TEST_TARGET)


__all__ = [
    "SECRET_NAME",
    "SETTINGS_KEY",
    "TEST_TARGET",
    "outbound_proxy_for",
    "proxy_scope",
    "run_test",
    "state_of",
    "stored",
    "stored_config",
]
