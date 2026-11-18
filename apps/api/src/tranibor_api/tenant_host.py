# SPDX-License-Identifier: Apache-2.0
"""An organisation owns a custom host (ADR 0175): the seam that says which organisation a host is.

The core knows no tenant host. An extension (ADR 0024: the enterprise edition) stores which hosts an
organisation claimed, proves each claim, and declares ONE resolver:

    from tranibor_api.tenant_host import declare_host_resolver

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

The reverse seam, `declare_org_host_resolver(resolver(session, slug) -> host | None)`, says which ONE verified
host the links built for an organisation use (`org_base_url`, S74-03). The core keeps its answer only if the
forward resolver gives the same organisation back for it, so an unverified or foreign host never reaches
a link.

The resolver must answer only for a host whose owner PROVED it (a DNS challenge): the core trusts the
mapping and has no other source for it. Without a resolver (the community edition) every host is the
instance's.
"""

from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from structlog import get_logger

from .config import Settings, get_settings
from .theme import ORG_SLUG

logger = get_logger("choregos.tenant_host")

_RESOLVER: dict[str, Any] = {}
_ORG_RESOLVER: dict[str, Any] = {}

#: How long the runtime allowlists (origin guard, internal-API guard) remember what the resolver said
#: about a host, an answer and a refusal alike: a claim proved or withdrawn takes effect within this
#: delay. Bounded in size so that a client sending many distinct `Host` values cannot grow it.
CACHE_TTL_S = 30.0
CACHE_MAX_ENTRIES = 1024
_CACHE: dict[str, tuple[float, str | None]] = {}
#: What `org_host` remembered per organisation slug, on the same terms (ADR 0175, S74-03 amendment).
_ORG_CACHE: dict[str, tuple[float, str | None]] = {}

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


def declare_org_host_resolver(resolver: Any) -> None:
    """Declares `resolver(session, slug: str) -> str | None`, sync or async: the ONE host the organisation
    `slug` is reached on, `None` when it has none (ADR 0175, S74-03 amendment).

    The reverse of `declare_host_resolver`, for the links the platform builds for an organisation. When an
    organisation holds several verified hosts the extension chooses deterministically (the enterprise edition:
    the one verified first, ties broken by name). The core never trusts the answer on its own: it keeps it
    only if the forward resolver gives the same organisation back for that host (`org_host`).
    """
    if not callable(resolver):
        raise TypeError("an organisation's host resolver is a callable (session, slug) -> str | None")
    _ORG_RESOLVER["resolver"] = resolver


def has_host_resolver() -> bool:
    return "resolver" in _RESOLVER


def reset() -> None:
    """For tests: no tenant host, every host is the instance's."""
    _RESOLVER.clear()
    _ORG_RESOLVER.clear()
    _CACHE.clear()
    _ORG_CACHE.clear()


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


def request_host(headers: Any) -> str | None:
    """The DNS name the request was made for: `X-Forwarded-Host` (first entry), else `Host`; `None` when it
    is not a DNS name. The console's relay of `/api/v1` rewrites `Host` and says where the request came in
    with `X-Forwarded-Host`. Client-controlled: it selects anything only once `resolve_host` has matched
    it against the verified table, and the ingress must overwrite a client-supplied value."""
    return normalise_host(headers.get("x-forwarded-host") or headers.get("host"))


@dataclass(frozen=True, slots=True)
class TenantDoor:
    """A verified host and the organisation it belongs to (ADR 0175)."""

    host: str
    org: str


async def tenant_door(session: Any, headers: Any) -> TenantDoor | None:
    """The verified tenant host the request arrived on, or `None` (the instance's own host, an unknown
    host, an unverified claim, no resolver)."""
    host = request_host(headers)
    org = await resolve_host(session, host) if host else None
    return TenantDoor(host, org) if host and org else None


async def verified_host_cached(host: str | None) -> str | None:
    """`resolve_host` for the guards that run before any route: own session, briefly cached.

    The slug of the organisation that owns `host` (already a DNS name, as `normalise_host` gives it), or
    `None`. A cache hit costs nothing; a miss asks the resolver once per `CACHE_TTL_S`. Never raises.
    """
    if not host or "resolver" not in _RESOLVER or normalise_host(host) != host:
        return None
    now = time.monotonic()
    hit = _CACHE.get(host)
    if hit is not None and hit[0] > now:
        return hit[1]
    from .db.session import ALL_ORGS, get_sessionmaker, limit_to_organisations

    org: str | None = None
    try:
        async with get_sessionmaker()() as session:
            await limit_to_organisations(session, ALL_ORGS)
            org = await resolve_host(session, host)
    except Exception:
        logger.exception("tenant_host.lookup_failed", host=host)
        return None  # not cached: the next request asks again
    if len(_CACHE) >= CACHE_MAX_ENTRIES:
        _CACHE.clear()
    _CACHE[host] = (now + CACHE_TTL_S, org)
    return org


async def _verified_primary_host(session: Any, slug: str) -> str | None:
    """The organisation's primary host as the extension says it, kept only if it is a DNS name that the
    FORWARD resolver, asked now, still gives to this very organisation. Never raises."""
    reverse = _ORG_RESOLVER.get("resolver")
    if reverse is None or "resolver" not in _RESOLVER or not ORG_SLUG.match(slug):
        return None
    try:
        found = reverse(session, slug)
        if inspect.isawaitable(found):
            found = await found
    except Exception:
        logger.exception("tenant_host.org_resolver_failed", org=slug)
        return None
    if found is None:
        return None
    host = normalise_host(found) if isinstance(found, str) else None
    if host is None or host != found:
        logger.error("tenant_host.invalid_org_host", org=slug)
        return None
    # A link must never point to an unverified host, nor to another organisation's: ask the forward side.
    return host if await resolve_host(session, host) == slug else None


async def org_host(slug: str) -> str | None:
    """The verified host links for organisation `slug` should use, or `None` (the instance's host).

    Asks in a session of its own, over every organisation, like `verified_host_cached`: the claims are the
    platform's, whoever the request's caller is. Memoised for `CACHE_TTL_S` per slug, a refusal as well
    (bounded at `CACHE_MAX_ENTRIES`): a host verified
    or withdrawn changes the links within that delay. Never raises.
    """
    if "resolver" not in _ORG_RESOLVER:
        return None
    now = time.monotonic()
    hit = _ORG_CACHE.get(slug)
    if hit is not None and hit[0] > now:
        return hit[1]
    host: str | None = None
    try:
        from .db.session import ALL_ORGS, get_sessionmaker, limit_to_organisations

        async with get_sessionmaker()() as session:
            await limit_to_organisations(session, ALL_ORGS)
            host = await _verified_primary_host(session, slug)
    except Exception:
        logger.exception("tenant_host.org_lookup_failed", org=slug)
        return None  # not cached: the next link asks again
    if len(_ORG_CACHE) >= CACHE_MAX_ENTRIES:
        _ORG_CACHE.clear()
    _ORG_CACHE[slug] = (now + CACHE_TTL_S, host)
    return host


def host_base_url(public_url: str, host: str) -> str:
    """The origin of a verified host: the instance's scheme (`public_url`'s), the host, no port."""
    return f"{urlsplit(public_url).scheme}://{host}"


async def org_base_url(
    slug: str | None, settings: Settings | None = None, *, public_url: str | None = None
) -> str:
    """The console's base URL (no trailing slash) for a link built for organisation `slug`: its verified
    host when it has one (`org_host`), else the instance's `public_url`, which stays valid (ADR 0175 point 4).
    """
    public = public_url or (settings or get_settings()).public_url
    host = await org_host(slug) if slug else None
    return host_base_url(public, host) if host else public.rstrip("/")


async def request_base_url(headers: Any, settings: Settings | None = None) -> str:
    """The base URL of the door a request came in on: the verified tenant host it was made for, else the
    instance's. For what must equal the address the caller typed (the MCP resource metadata)."""
    settings = settings or get_settings()
    host = request_host(headers)
    if host and await verified_host_cached(host):
        return host_base_url(settings.public_url, host)
    return settings.public_url.rstrip("/")


async def org_base_url_of_id(session: Any, org_id: str | None, settings: Settings | None = None) -> str:
    """`org_base_url` for a caller that holds the organisation's id, not its slug (the slug read is the
    caller's own session, as everywhere else)."""
    from .db.models import Organization

    organisation = await session.get(Organization, org_id) if org_id else None
    return await org_base_url(organisation.slug if organisation is not None else None, settings)
