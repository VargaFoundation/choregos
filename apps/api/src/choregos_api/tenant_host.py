# SPDX-License-Identifier: Apache-2.0
"""An organisation owns a custom host (ADR 0175): the seam that says which organisation a host is.

The core knows no tenant host. An extension (ADR 0024: the enterprise edition) stores which hosts an
organisation claimed, proves each claim, and declares ONE resolver:

    from choregos_api.tenant_host import declare_host_resolver

    async def resolve(session, host):
        # host: lower-case, no port, no final dot, a DNS name (`agentic.client.com`)
        row = await my_storage.verified(session, host)
        return row.org_slug if row else None      # None: not a tenant host, the instance's default

    def plug():
        declare_host_resolver(resolve)

What the core guarantees, whatever the resolver returns, and why:

- **The resolver is only asked about a well-formed DNS name.** `Host` and `X-Forwarded-Host` are
  client-controlled; an IP literal, a name without a dot, an overlong or non-ASCII value (an IDN
  travels as its `xn--` form) never reaches the extension's storage.
- **The answer is a slug or nothing.** Anything else the resolver returns (a path, an uppercase
  name, a number) is "no organisation": a host never selects what a slug could not.
- **A failing resolver is "no organisation".** The routes that read it are public and draw the first
  paint of the sign-in page: a broken lookup must not take the console down, it gives the instance's
  look (and the failure is logged).

The resolver must answer only for a host whose owner PROVED it (a DNS challenge): the core trusts the
mapping and has no other source for it. Without a resolver (the community edition) every host is the
instance's.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from structlog import get_logger

from .theme import ORG_SLUG

logger = get_logger("choregos.tenant_host")

_RESOLVER: dict[str, Any] = {}

#: A DNS name of at least two labels (a custom host always has a dot), each 1-63 characters of
#: letters, digits and hyphens (never at an end), 253 in all; `xn--` is how an IDN travels.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"^(?:{_LABEL}\.)+{_LABEL}$")
_IPV4 = re.compile(r"^[0-9.]+$")
MAX_HOST_CHARS = 253


def declare_host_resolver(resolver: Any) -> None:
    """Declares `resolver(session, host: str) -> str | None`, sync or async: the slug of the
    organisation that owns `host`, `None` when it is not a tenant host.

    One resolver per deployment: a second declaration replaces the first. An exception is not a
    `None` for the extension, but the core treats it as one (see the module's docstring).
    """
    if not callable(resolver):
        raise TypeError("a host resolver is a callable (session, host) -> str | None")
    _RESOLVER["resolver"] = resolver


def has_host_resolver() -> bool:
    return "resolver" in _RESOLVER


def reset() -> None:
    """For tests: no tenant host, every host is the instance's."""
    _RESOLVER.clear()


def normalise_host(raw: str | None) -> str | None:
    """The DNS name a `Host` / `X-Forwarded-Host` value designates, or `None` when it is not one.

    A forwarded list (`a.example, b.example`) is read at its first entry, the client-facing one.
    """
    if not raw:
        return None
    host = raw.split(",", 1)[0].strip().lower()
    if host.count(":") == 1:  # `name:port`; an IPv6 literal has several colons and is refused below
        host = host.partition(":")[0]
    host = host.rstrip(".")
    if not host or len(host) > MAX_HOST_CHARS or _IPV4.match(host) or not _HOST.match(host):
        return None
    return host


async def resolve_host(session: Any, raw_host: str | None) -> str | None:
    """The slug of the organisation that owns the host, or `None`. Never raises."""
    resolver = _RESOLVER.get("resolver")
    host = normalise_host(raw_host)
    if resolver is None or host is None:
        return None
    try:
        found = resolver(session, host)
        if inspect.isawaitable(found):
            found = await found
    except Exception:
        logger.exception("tenant_host.resolver_failed", host=host)
        return None
    if isinstance(found, str) and ORG_SLUG.match(found):
        return found
    if found is not None:
        logger.error("tenant_host.invalid_answer", host=host, answer=type(found).__name__)
    return None
