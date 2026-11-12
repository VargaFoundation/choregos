# SPDX-License-Identifier: Apache-2.0
"""An organisation's MCP gateway: the setting, its checks, the client it builds (S46-08, ADR 0105).

Choregos consumes a gateway the organisation already runs; it never is one. The setting is a key of
`Organization.settings`; its credential is sealed in the organisation's secret store (ADR 0058), is
write-only, and leaves the store only inside `client_config`'s headers provider, one request at a time.
Fail closed: a gateway that cannot be reached with its credential (no secrets key, the credential or the
assertion key lost) is a typed `GatewayRefusal`, never a call without it.

The endpoint is the one address an administrator types that the platform then calls from its own
network, so `check_endpoint` is an SSRF guard: https only, no credentials in the URL, and no address that
points inside the cluster unless the operator lists that host (`mcp_gateway_internal_hosts`).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Final
from urllib.parse import quote, urlsplit

import choregos_adapters
import httpx
from choregos_adapters import build, fakes_enabled
from choregos_adapters.mcp import ClientCredentials, TokenCache
from choregos_adapters.outpost import OutpostRefusal
from choregos_core.redaction import remember_secret
from choregos_core.secrets import SecretNotFound
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Organization, OrgConnector
from ..errors import ApiError, conflict, unprocessable
from ..schemas.mcp_gateway import McpGateway, McpGatewayAuth, McpGatewayPut, McpGatewayState
from ..secret_store import SecretStore, SecretStoreUnavailable, get_secret_store, resolved_configuration
from .egress import Target, targets_of_urls
from .mcp_gateway_identity import (
    CallIdentity,
    GatewayRefusal,
    assertion_state,
    issuer_of,
    mint_assertion,
    signing_key,
)
from .platform_network import connector_host, host_of, with_identity

SETTINGS_KEY = "mcp_gateway"
#: Where the credential is sealed (ADR 0058). One per organisation, since there is one gateway.
SECRET_NAME = "org/mcp-gateway/credential"  # noqa: S105 - a name in the secret store, not a secret
GATEWAY_TYPE = "mcp-gateway"
#: The type of a connector reached through an outpost (ADR 0149): `services/outpost_route.py`.
OUTPOST_TYPE = "mcp-outpost"
ORIGIN = "mcp-gateway"
PRIVATE_SUFFIXES = (".svc", ".svc.cluster.local", ".cluster.local", ".local", ".internal")
#: The placeholder an endpoint carries for the server's name.
SERVER_PLACEHOLDER = "{server}"
_HOST_NAME = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
_NEEDS_SECRET = ("bearer", "header", "oauth_client_credentials")

Provider = Callable[[bool], Awaitable[dict[str, str]]]
#: The gateway's OAuth tokens, by organisation (ADR 0105).
_TOKENS = TokenCache(label="the MCP gateway's token endpoint")
_token_transport: httpx.AsyncBaseTransport | None = None


class GatewayNotConfigured(GatewayRefusal):
    code: ClassVar[str] = "gateway_not_configured"


class GatewayCredentialMissing(GatewayRefusal):
    code: ClassVar[str] = "gateway_credential_missing"


class GatewayRequired(GatewayRefusal):
    code: ClassVar[str] = "gateway_required"


@dataclass(frozen=True)
class GatewaySetting:
    """The setting as stored: everything `McpGateway` returns except `secret_set`."""

    mode: str
    endpoint: str
    routing: str
    server_header: str | None
    auth: McpGatewayAuth
    audience: str | None
    identity_header: str | None
    timeout_s: float

    @classmethod
    def from_stored(cls, data: Mapping[str, Any]) -> GatewaySetting:
        return cls(
            mode=str(data["mode"]),
            endpoint=str(data["endpoint"]),
            routing=str(data["routing"]),
            server_header=data.get("server_header"),
            auth=McpGatewayAuth.model_validate(data["auth"]),
            audience=data.get("audience"),
            identity_header=data.get("identity_header"),
            timeout_s=float(data["timeout_s"]),
        )

    def to_stored(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "endpoint": self.endpoint,
            "routing": self.routing,
            "server_header": self.server_header,
            "auth": self.auth.model_dump(mode="json"),
            "audience": self.audience,
            "identity_header": self.identity_header,
            "timeout_s": self.timeout_s,
        }

    @classmethod
    def from_put(cls, body: McpGatewayPut) -> GatewaySetting:
        return cls(
            mode=body.mode,
            endpoint=body.endpoint,
            routing=body.routing,
            server_header=body.server_header,
            auth=body.auth,
            audience=body.audience,
            identity_header=body.identity_header,
            timeout_s=body.timeout_s,
        )


def stored(organisation: Organization | None) -> GatewaySetting | None:
    """The setting as stored (no credential: that one is in the secret store), or `None`."""
    if organisation is None:
        return None
    found = (organisation.settings or {}).get(SETTINGS_KEY)
    if not isinstance(found, dict) or not isinstance(found.get("gateway"), dict):
        return None
    return GatewaySetting.from_stored(found["gateway"])


# ───────────────────────────── the SSRF guard ─────────────────────────────


def _bad(loc: list[str], msg: str) -> Exception:
    return unprocessable(msg, [{"loc": loc, "msg": msg}])


def _listed(host: str, internal_hosts: Iterable[str]) -> bool:
    return host in {h.strip().lower().rstrip(".") for h in internal_hosts}


def _points_inside(host: str) -> str | None:
    """Why `host` is an address inside (or an alias of one), or `None` for a public name."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return "an IP address"
    labels = host.split(".")
    # `127.1`, `2130706433` and `0x7f.1` are IP literals to the resolver, though not to `ipaddress`:
    # a top-level label is never a number.
    if re.fullmatch(r"(0x[0-9a-f]*|[0-9]+)", labels[-1]):
        return "a numeric address"
    if len(labels) == 1 or host == "localhost" or host.endswith(".localhost"):
        return "a name without a domain"
    if any(host.endswith(suffix) for suffix in PRIVATE_SUFFIXES):
        return "a private name"
    return None


def _check_address(url: str, loc: list[str], internal_hosts: Iterable[str], *, what: str) -> str:
    """The host of `url`, or 422. `{server}` is a placeholder, not part of the address."""
    try:
        parts = urlsplit(url.replace(SERVER_PLACEHOLDER, "server"))
        host = (parts.hostname or "").strip().lower().rstrip(".")
        parts.port  # noqa: B018 - raises ValueError on a port out of range
    except ValueError as invalid:
        raise _bad(loc, f"{what} is not a valid address") from invalid
    if parts.scheme not in {"http", "https"} or not host:
        raise _bad(loc, f"{what} must be an http(s) address with a host")
    if parts.username is not None or parts.password is not None:
        raise _bad(loc, f"{what} must not carry credentials")
    if parts.query or parts.fragment:
        raise _bad(loc, f"{what} must not carry a query or a fragment")
    listed = _listed(host, internal_hosts)
    if parts.scheme == "http" and not listed:
        raise _bad(loc, f"{what} must be https (plain http only for a host the operator lists)")
    if not listed:
        inside = _points_inside(host)
        if inside:
            raise _bad(
                loc,
                f"{what} points at {inside} (`{host}`): an operator lists such a host in "
                "CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS",
            )
    if not _is_ip(host) and not _HOST_NAME.fullmatch(host):
        raise _bad(loc, f"{what}'s host `{host}` is not a valid ASCII host name")
    return host


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def check_endpoint(url: str, routing: str, internal_hosts: Iterable[str]) -> None:
    """422 (`loc: body.endpoint`) for an endpoint that points inside, is plain http, carries
    credentials, or contradicts its routing."""
    loc = ["body", "endpoint"]
    _check_address(url, loc, internal_hosts, what="the endpoint")
    count = url.count(SERVER_PLACEHOLDER)
    if routing == "header":
        if count:
            raise _bad(loc, "with header routing the endpoint carries no `{server}`")
        return
    authority = url.find("://") + 3
    path_start = url.find("/", authority)
    if count != 1 or path_start < 0 or url.find(SERVER_PLACEHOLDER) < path_start:
        raise _bad(loc, "with path routing the endpoint carries exactly one `{server}`, in its path")


def check_token_url(url: str, internal_hosts: Iterable[str]) -> None:
    """The same address rule for the token endpoint (`loc: body.auth.token_url`)."""
    _check_address(url, ["body", "auth", "token_url"], internal_hosts, what="the token endpoint")


def validate_put(body: McpGatewayPut, previous: GatewaySetting | None, secret_known: bool) -> None:
    """422 for a setting that contradicts itself, or that would leave the gateway without its credential."""
    auth = body.auth

    def refuse(loc: list[str], msg: str) -> Exception:
        return _bad(["body", *loc], msg)

    if body.routing == "header" and not body.server_header:
        raise refuse(["server_header"], "header routing needs `server_header`")
    if auth.type == "header" and not auth.header_name:
        raise refuse(["auth", "header_name"], "the `header` auth needs `header_name`")
    if auth.type == "oauth_client_credentials":
        if not auth.token_url:
            raise refuse(["auth", "token_url"], "the OAuth client credentials need `token_url`")
        if not auth.client_id:
            raise refuse(["auth", "client_id"], "the OAuth client credentials need `client_id`")
    if auth.type == "jwt_assertion" and not body.audience:
        raise refuse(["audience"], "the `jwt_assertion` auth needs `audience`")
    if body.identity_header and not body.audience:
        raise refuse(["audience"], "an `identity_header` needs `audience`")
    if auth.type == "jwt_assertion" and body.secret is not None:
        raise refuse(["secret"], "the `jwt_assertion` auth is signed by Choregos: it has no secret")
    if auth.type in _NEEDS_SECRET and body.secret is None:
        unchanged = previous is not None and previous.auth.type == auth.type and secret_known
        if not unchanged:
            raise refuse(["secret"], f"the `{auth.type}` auth needs a secret: none is stored for it")
    if not 1 <= body.timeout_s <= 120:
        raise refuse(["timeout_s"], "the timeout is between 1 and 120 seconds")


# ───────────────────────────── hosts and URLs ─────────────────────────────


def server_url(setting: GatewaySetting, server: str) -> str:
    """The URL of one server at the gateway: `{server}` replaced in the path, or the endpoint as is."""
    if setting.routing == "path":
        return setting.endpoint.replace(SERVER_PLACEHOLDER, quote(server, safe=""))
    return setting.endpoint


def _addresses(setting: GatewaySetting) -> list[str]:
    urls = [setting.endpoint.replace(SERVER_PLACEHOLDER, "x")]
    if setting.auth.token_url:
        urls.append(setting.auth.token_url)
    return urls


def egress_targets(setting: GatewaySetting) -> list[Target]:
    """What the setting opens: the endpoint and the token endpoint, for the pods that call them. A
    governed write calls the gateway from the workers (ADR 0008); the runners never do (ADR 0105)."""
    urls = _addresses(setting)
    return targets_of_urls(urls, "api") + targets_of_urls(urls, "workers")


def gateway_host(organisation: Organization) -> str | None:
    setting = stored(organisation)
    return host_of(setting.endpoint.replace(SERVER_PLACEHOLDER, "x")) if setting else None


def hosts_in_use(setting: GatewaySetting | None) -> set[str]:
    """Every host the setting reaches: the endpoint's and the token endpoint's (the runner guard, S46-19)."""
    if setting is None:
        return set()
    return {host for url in _addresses(setting) if (host := host_of(url))}


# ───────────────────────────── the OAuth token ─────────────────────────────


def clear_token_cache() -> None:
    _TOKENS.clear()


def set_token_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Tests: the transport the token request goes through. `None`: the default."""
    global _token_transport  # noqa: PLW0603 - a test seam, like the platform's other fakes
    _token_transport = transport


def _transport() -> httpx.AsyncBaseTransport | None:
    if _token_transport is not None:
        return _token_transport
    if fakes_enabled():
        transport: httpx.AsyncBaseTransport = choregos_adapters.fake_mcp_gateway().transport()
        return transport
    return None


async def _oauth_token(org_id: str, setting: GatewaySetting, client_secret: str, refresh: bool) -> str:
    """The client-credentials token, cached until shortly before it expires (the shared
    `choregos_adapters.mcp.client_credentials`, which a direct `mcp` connector uses too). The request goes
    through the guarded transport: it is checked against the organisation's egress list like any other."""
    auth = setting.auth
    parts = urlsplit(setting.endpoint.replace(SERVER_PLACEHOLDER, "x"))
    credentials = ClientCredentials(
        token_url=auth.token_url or "",
        client_id=auth.client_id or "",
        client_secret=client_secret,
        scope=auth.scope,
        audience=auth.token_audience,
        resource=auth.resource or f"{parts.scheme}://{parts.netloc}",
    )
    return await _TOKENS.token(
        credentials, refresh=refresh, transport=_transport(), timeout_s=setting.timeout_s, partition=org_id
    )


# ───────────────────────────── the client ─────────────────────────────


async def _credential(session: AsyncSession, organisation: Organization, store: SecretStore | None) -> str:
    try:
        values = await (store or get_secret_store()).get_many(session, organisation.id, [SECRET_NAME])
    except (SecretNotFound, SecretStoreUnavailable) as lost:
        raise GatewayCredentialMissing(
            f"the credential of the MCP gateway of {organisation.slug} is not available: "
            "an administrator sets it again on the Administration page"
        ) from lost
    secret = values[SECRET_NAME]
    remember_secret(secret)
    return secret


async def client_config(
    session: AsyncSession,
    organisation: Organization,
    server: str,
    identity: CallIdentity,
    store: SecretStore | None = None,
) -> dict[str, Any]:
    """The adapter configuration of one server behind the gateway. The mode is NOT checked here: the
    callers decide whether the gateway applies (S46-09). Raises `GatewayRefusal` when it cannot be built."""
    setting = stored(organisation)
    if setting is None:
        raise GatewayNotConfigured(f"{organisation.slug} has no MCP gateway: an administrator sets one")
    auth_type = setting.auth.type
    secret = await _credential(session, organisation, store) if auth_type in _NEEDS_SECRET else ""
    signing: tuple[str, str] | None = None
    if auth_type == "jwt_assertion" or setting.identity_header:
        signing = await signing_key(session, organisation, store)
    issuer, audience = issuer_of(organisation), setting.audience or ""
    org_id = organisation.id

    def assertion() -> str:
        assert signing is not None
        return mint_assertion(signing[0], signing[1], issuer=issuer, audience=audience, identity=identity)

    async def provider(refresh: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
        elif auth_type == "header":
            headers[setting.auth.header_name or ""] = secret
        elif auth_type == "oauth_client_credentials":
            headers["Authorization"] = f"Bearer {await _oauth_token(org_id, setting, secret, refresh)}"
        else:
            headers["Authorization"] = f"Bearer {assertion()}"
        if setting.identity_header and auth_type != "jwt_assertion":
            headers[setting.identity_header] = assertion()
        return headers

    return {
        "route": "gateway",
        "gateway_url": server_url(setting, server),
        "gateway_headers": {setting.server_header: server}
        if setting.routing == "header" and setting.server_header
        else {},
        "headers_provider": provider,
        "timeout_s": setting.timeout_s,
    }


async def client_for_server(
    session: AsyncSession,
    organisation: Organization,
    server: str,
    identity: CallIdentity,
    store: SecretStore | None = None,
) -> Any:
    """The MCP client of `server` through the organisation's gateway (what S46-09 reuses)."""
    config = await client_config(session, organisation, server, identity, store)
    return build(
        "mcp", GATEWAY_TYPE, await with_identity(session, organisation.id, {"server": server, **config})
    )


@dataclass(frozen=True)
class Route:
    """Where a connector's calls go: the host the network bounds judge, and whether it is the gateway's;
    or, through an outpost (ADR 0149 §3), the outpost's name and the endpoint alias, and no host: the
    platform opens no connection for such a call."""

    host: str | None
    via_gateway: bool
    outpost: str | None = None
    endpoint: str | None = None

    @property
    def via_outpost(self) -> bool:
        return self.outpost is not None


#: What the seam refuses a route with, before a call leaves or while it is made: the MCP gateway's
#: refusals (ADR 0105 §7) and an outpost's (ADR 0149 §7). Each caller turns one into its own.
RouteRefusal: Final = (GatewayRefusal, OutpostRefusal)


def tool_error(refused: GatewayRefusal | OutpostRefusal) -> tuple[int, dict[str, Any]]:
    """What a refusal of the route is for whoever called a tool: a status and a tool-error body. The
    gateway's are a 403 of type `network_policy`, which no booking counts; an outpost's are one per
    class (`outpost_route.tool_error`)."""
    if isinstance(refused, OutpostRefusal):
        from .outpost_route import tool_error as outpost_tool_error

        return outpost_tool_error(refused)
    return 403, {"error": str(refused), "type": "network_policy", "code": refused.code}


def route_problem(refused: GatewayRefusal | OutpostRefusal) -> ApiError:
    """What a refusal of the route is where a connector is declared or discovered: a 409 that carries
    its code, and for an outpost the contract's `OutpostConflict` (`outpost_route.problem`)."""
    if isinstance(refused, OutpostRefusal):
        from .outpost_route import problem

        return problem(refused)
    return conflict(str(refused), extra={"code": refused.code})


async def route_of(session: AsyncSession, instance: OrgConnector) -> Route:
    """How an organisation connector is reached, decided BEFORE anything leaves (ADR 0105, ADR 0149).

    - an `mcp-outpost` connector is reached through its outpost, which must be handed calls:
      `OutpostPaused` when it is paused or revoked, or outposts are switched off;
    - an `mcp-gateway` connector needs a gateway that is set and not `off`: `GatewayNotConfigured`;
    - in mode `required`, a direct `mcp` connector is not called: `GatewayRequired`. A plugin's server is
      exempt in every mode (ADR 0096): the platform deployed it, the organisation did not declare it.
      A connector at an outpost is exempt too (ADR 0149 §8): `required` exists so that the gateway is
      the one place that holds upstream credentials and decides the upstream network, and here the
      platform holds no upstream credential and opens no upstream connection: the outpost does both;
    - any other connector is reached as it always was.
    Every caller turns a `RouteRefusal` into its own refusal; none creates an action or a cost line."""
    if instance.kind == "mcp" and instance.type == OUTPOST_TYPE:
        from . import outpost_route

        reached = await outpost_route.route(session, instance)
        return Route(None, False, outpost=reached.name, endpoint=reached.endpoint)
    organisation = await session.get(Organization, instance.org_id)
    setting = stored(organisation)
    org_name = organisation.slug if organisation is not None else instance.org_id
    if instance.type == GATEWAY_TYPE:
        if setting is None or setting.mode == "off" or organisation is None:
            raise GatewayNotConfigured(
                f"`{instance.name}` goes through the MCP gateway of {org_name}, which has none (or it is "
                "off): an administrator sets it on the Administration page"
            )
        return Route(gateway_host(organisation), True)
    # The direct type alone: neither a plugin's server (ADR 0096) nor a connector at an outpost
    # (ADR 0149 §8, decided above) is a direct `mcp` connector.
    if (
        instance.kind == "mcp"
        and instance.type == "mcp"
        and instance.plugin_server is None
        and setting is not None
        and setting.mode == "required"
    ):
        raise GatewayRequired(
            f"{org_name} reaches its MCP servers only through its MCP gateway: the direct connector "
            f"`{instance.name}` is not called; declare it as an mcp-gateway connector"
        )
    return Route(connector_host(instance.config), False)


async def connector_client(
    session: AsyncSession,
    instance: OrgConnector,
    identity: Callable[[], Awaitable[CallIdentity]],
    *,
    effect_key: str | None = None,
) -> Any:
    """The client every organisation connector is called with (broker, governed writes, discovery).

    `route_of` decides first and raises a `RouteRefusal`. A gateway connector gets the gateway's address
    and headers, signed with `identity` (computed only here, so a direct call never pays for it); the
    connector's own `timeout_s` wins over the gateway's. A connector at an outpost gets a client with
    the same two methods and no connection behind them (ADR 0149 §3): its calls are placed at the
    outpost's rendezvous, with the names of `identity` as what the outpost may log. Any other
    connector, a plugin's server included (ADR 0096), is built by `plugin_servers.connector_client`
    exactly as before.

    `effect_key` is the key of the governed effect that makes the call, when one does: through an
    outpost a write is placed once under it and never delivered twice, whatever is retried (ADR 0149
    §5). A write that reaches an outpost's connector without one is not placed."""
    from . import plugin_servers

    if instance.kind == "mcp" and instance.type == OUTPOST_TYPE:
        from . import outpost_route

        return await outpost_route.client(session, instance, identity, effect_key=effect_key)
    route = await route_of(session, instance)
    if not route.via_gateway:
        return await plugin_servers.connector_client(session, instance)
    organisation = await session.get(Organization, instance.org_id)
    assert organisation is not None
    config = await with_identity(
        session,
        instance.org_id,
        await resolved_configuration(
            session, instance.org_id, instance.kind, instance.type, instance.config, instance.secret_refs
        ),
    )
    injected = await client_config(session, organisation, str(config["server"]), await identity())
    own = (instance.config or {}).get("timeout_s")
    config = {**config, **injected, "timeout_s": float(own) if own else injected["timeout_s"]}
    return build(instance.kind, instance.type, config)


async def check_declaration(
    session: AsyncSession, organisation: Organization, kind: str, type_name: str
) -> None:
    """At declaration (the one door, the catalogue install included): mode `required` declares no direct
    `mcp` connector, and an `mcp-gateway` one needs a gateway that is set and not `off` (ADR 0105).

    Mode `required` refuses the type `mcp` and no other. A connector at an outpost (`mcp-outpost`) is
    declared in every mode (ADR 0149 §8, amending ADR 0105 §5): the mode keeps the upstream credentials
    and the upstream network at the gateway, and through an outpost the platform holds neither. What
    such a declaration needs is its outpost (`outpost_route.declared_outpost`)."""
    setting = stored(organisation)
    if kind == "mcp" and type_name == "mcp" and setting is not None and setting.mode == "required":
        raise conflict(
            f"{organisation.slug} reaches its MCP servers only through its MCP gateway: declare an "
            "mcp-gateway connector, not a direct one",
            extra={"code": GatewayRequired.code},
        )
    if type_name == GATEWAY_TYPE and (setting is None or setting.mode == "off"):
        raise conflict(
            f"{organisation.slug} has no MCP gateway (or it is off): an administrator sets it on the "
            "Administration page before an mcp-gateway connector is declared",
            extra={"code": GatewayNotConfigured.code},
        )


async def state_of(
    session: AsyncSession, organisation: Organization, store: SecretStore | None = None
) -> McpGatewayState:
    assertion = assertion_state(organisation)
    setting = stored(organisation)
    if setting is None:
        return McpGatewayState(assertion=assertion)
    meta = await (store or get_secret_store()).meta(session, organisation.id, [SECRET_NAME])
    raw = (organisation.settings or {}).get(SETTINGS_KEY) or {}
    return McpGatewayState(
        gateway=McpGateway(
            mode=setting.mode,
            endpoint=setting.endpoint,
            routing=setting.routing,
            server_header=setting.server_header,
            auth=setting.auth,
            audience=setting.audience,
            identity_header=setting.identity_header,
            timeout_s=setting.timeout_s,
            secret_set=SECRET_NAME in meta,
        ),
        updated_by=raw.get("updated_by"),
        updated_at=raw.get("updated_at"),
        assertion=assertion,
    )


__all__ = [
    "GATEWAY_TYPE",
    "ORIGIN",
    "OUTPOST_TYPE",
    "SECRET_NAME",
    "SETTINGS_KEY",
    "GatewayCredentialMissing",
    "GatewayNotConfigured",
    "GatewayRequired",
    "GatewaySetting",
    "Route",
    "RouteRefusal",
    "check_declaration",
    "check_endpoint",
    "check_token_url",
    "clear_token_cache",
    "client_config",
    "client_for_server",
    "connector_client",
    "egress_targets",
    "gateway_host",
    "hosts_in_use",
    "route_of",
    "route_problem",
    "server_url",
    "set_token_transport",
    "state_of",
    "stored",
    "tool_error",
    "validate_put",
]
