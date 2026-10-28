# SPDX-License-Identifier: Apache-2.0
"""A fake MCP gateway, scriptable (ADR 0005): an organisation's own MCP gateway as Choregos sees
it. Choregos consumes such a gateway and never is one (ADR 0105).

This is not a product, and Choregos never serves it to an organisation: it exists so that the
gateway connector, its credentials and its failures are tested without a real gateway, and so
that the local install has a gateway in front of the demo fakes.

What it does:
- it fronts several MCP servers, fake ones in memory (`FakeMcpServer`) or real ones over HTTP,
  and picks one by the request's path (`/{server}/mcp`) or by a header (`X-MCP-Server`). An
  unknown server goes to the fallback, resolved again on every request, or is a 404;
- it checks the caller's credential the way a gateway does: a static bearer token, an API-key
  header, an OAuth client-credentials token from its own `/oauth/token`, or a signed JWT
  assertion verified against a JWKS, fetched again once when the key id is unknown (a rotation).
  An identity header, when one is set, is verified the same way: a bare, unsigned claim of who
  is calling is refused;
- it records every call with who called and the identity claims it verified (`calls`), every
  token request (`token_requests`) and every refusal (`refusals`);
- it can be stateless, forget the sessions it issued (as another replica would), and revoke the
  tokens it issued.

The gateway, never Choregos, holds the upstream servers' credentials: the caller's credential is
dropped, and the upstream receives the gateway's own (`GatewayUpstream.token`).

Served alone, as the local install's gateway: `python -m choregos_adapters.fakes.mcp_gateway`,
on port `CHOREGOS_FAKE_GATEWAY_PORT` (8091 by default), configured by the
`CHOREGOS_FAKE_GATEWAY_*` variables `application()` reads.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, cast, get_args
from urllib.parse import parse_qs

import httpx
import jwt
from choregos_core.mcp import VERSIONS

from .mcp import FakeMcpServer

AuthMode = Literal["none", "bearer", "header", "oauth_client_credentials", "jwt_assertion"]
Routing = Literal["path", "header"]
AUTH_MODES: tuple[str, ...] = get_args(AuthMode)
ROUTINGS: tuple[str, ...] = get_args(Routing)

#: An assertion that lives longer than this is refused: a signed assertion is minted per call.
MAX_ASSERTION_LIFETIME_S = 120
#: The claims an assertion must carry; `aud` too when the gateway names its audience.
REQUIRED_CLAIMS = ["exp", "iat", "jti", "sub"]
#: Never forwarded upstream as they came: the transport's own, and the session the gateway swaps.
_HOP_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection", "mcp-session-id"})
_UPSTREAM_TIMEOUT_S = 30.0

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class GatewayUpstream:
    """One server behind the gateway: a fake in memory, or an MCP endpoint over HTTP."""

    server: FakeMcpServer | None = None
    url: str = ""
    #: The upstream credential the gateway holds, sent as `Authorization: Bearer <token>`.
    token: str = ""


@dataclass
class GatewayCall:
    """A request the gateway let through: to which server, and who it said was calling."""

    server: str
    method: str
    #: The request's headers as they arrived, names lowercased.
    headers: dict[str, str]
    #: The verified claims of the caller's assertion (`jwt_assertion` mode).
    claims: dict[str, Any] | None = None
    #: The verified claims of the identity header's assertion, when the gateway reads one.
    identity_header_claims: dict[str, Any] | None = None


@dataclass
class FakeMcpGateway:
    upstreams: dict[str, GatewayUpstream] = field(default_factory=dict)
    #: Where an unknown server goes; a callable is called again on every request.
    fallback: FakeMcpServer | Callable[[], FakeMcpServer] | None = None
    routing: Routing = "path"
    path_template: str = "/{server}/mcp"
    server_header: str = "X-MCP-Server"
    auth: AuthMode = "bearer"
    #: The credential of the `bearer` and `header` modes; empty refuses every caller.
    token: str = ""
    auth_header: str = "X-Api-Key"
    client_id: str = ""
    client_secret: str = ""
    token_ttl_s: int = 300
    #: The JWKS the assertions are verified against, given as is, or fetched from `jwks_url`.
    jwks: dict[str, Any] | None = None
    jwks_url: str = ""
    #: The transport `jwks_url` is fetched through (tests); `None` is the network.
    jwks_transport: httpx.AsyncBaseTransport | None = None
    audience: str = ""
    issuer: str = ""
    #: A header carrying a second signed assertion: on whose behalf the caller calls.
    identity_header: str = ""
    stateless: bool = False
    #: That many upcoming sessions are forgotten right after being issued.
    forget_after_initialize: int = 0
    calls: list[GatewayCall] = field(default_factory=list)
    token_requests: list[dict[str, str]] = field(default_factory=list)
    refusals: list[tuple[int, str]] = field(default_factory=list)
    #: issued token -> its expiry, on the monotonic clock
    _issued: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _seen_jti: set[str] = field(default_factory=set, init=False, repr=False)
    #: gateway session -> (server, upstream session)
    _sessions: dict[str, tuple[str, str | None]] = field(default_factory=dict, init=False, repr=False)
    #: server -> its upstream session, when the gateway is stateless
    _stateless_upstream: dict[str, str | None] = field(default_factory=dict, init=False, repr=False)
    _counter: int = field(default=0, init=False, repr=False)
    _fetched_jwks: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def revoke_tokens(self) -> None:
        """Every token issued so far stops opening the gateway."""
        self._issued.clear()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.respond)

    async def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/oauth/token":
            return self._token_endpoint(request)
        if path == "/healthz":
            return httpx.Response(200, json={"ok": True})
        name = self._server_of(request)
        upstream = self._upstream(name)
        if name is None or upstream is None:
            return self._refuse(404, "unknown server")
        if request.method != "POST":
            return httpx.Response(405, json={"error": "an MCP request is a POST"})
        body = _json_body(request)
        if body is None:
            return httpx.Response(400, json={"error": "the body is not a JSON-RPC message"})
        identity = await self._authenticate(request)
        if isinstance(identity, str):
            return self._refuse(401, identity, {"WWW-Authenticate": "Bearer"})
        method = str(body.get("method", ""))
        session = request.headers.get("mcp-session-id")
        if method != "initialize" and session is not None and not self._knows(name, session):
            return self._refuse(404, "unknown session")
        self.calls.append(
            GatewayCall(
                server=name,
                method=method,
                headers={key.lower(): value for key, value in request.headers.items()},
                claims=identity[0],
                identity_header_claims=identity[1],
            )
        )
        return await self._forward(name, upstream, request, body)

    def _refuse(self, status: int, reason: str, headers: dict[str, str] | None = None) -> httpx.Response:
        self.refusals.append((status, reason))
        return httpx.Response(status, json={"error": reason}, headers=headers)

    # ── the token endpoint ──────────────────────────────────────────────────────────────────────

    def _token_endpoint(self, request: httpx.Request) -> httpx.Response:
        form = {
            key: values[0] for key, values in parse_qs(request.content.decode("utf-8", "replace")).items()
        }
        if not self._client_matches(request.headers.get("authorization", "")):
            return self._refuse(401, "invalid_client", {"WWW-Authenticate": 'Basic realm="token"'})
        if form.get("grant_type") != "client_credentials":
            return self._refuse(400, "unsupported_grant_type")
        self.token_requests.append({key: form.get(key, "") for key in ("scope", "resource", "audience")})
        token = uuid.uuid4().hex
        self._issued[token] = time.monotonic() + self.token_ttl_s
        return httpx.Response(
            200, json={"access_token": token, "token_type": "Bearer", "expires_in": self.token_ttl_s}
        )

    def _client_matches(self, authorization: str) -> bool:
        scheme, _, encoded = authorization.partition(" ")
        if scheme.lower() != "basic" or not self.client_id:
            return False
        try:
            pair = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
        except ValueError:
            return False
        client_id, _, secret = pair.partition(":")
        return _same(client_id, self.client_id) and _same(secret, self.client_secret)

    # ── routing ─────────────────────────────────────────────────────────────────────────────────

    def _server_of(self, request: httpx.Request) -> str | None:
        """The server the request names; `None` when the path is not an MCP endpoint."""
        if self.routing == "header":
            return str(request.headers.get(self.server_header, ""))
        before, _, after = self.path_template.partition("{server}")
        match = re.fullmatch(f"{re.escape(before)}(?P<server>[^/]+){re.escape(after)}", request.url.path)
        return str(match.group("server")) if match else None

    def _upstream(self, name: str | None) -> GatewayUpstream | None:
        if name is None:
            return None
        if name in self.upstreams:
            return self.upstreams[name]
        if self.fallback is None:
            return None
        server = self.fallback if isinstance(self.fallback, FakeMcpServer) else self.fallback()
        return GatewayUpstream(server=server)

    def _knows(self, name: str, session: str) -> bool:
        entry = self._sessions.get(session)
        return entry is not None and entry[0] == name

    # ── authentication ──────────────────────────────────────────────────────────────────────────

    async def _authenticate(
        self, request: httpx.Request
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None] | str:
        """The verified claims (caller's, identity header's), or why the request is refused."""
        claims = await self._credential(request)
        if isinstance(claims, str):
            return claims
        identity_claims: dict[str, Any] | None = None
        if self.identity_header:
            verified = await self._verify_assertion(request.headers.get(self.identity_header, ""))
            if isinstance(verified, str):
                return f"identity header: {verified}"
            identity_claims = verified
        return claims, identity_claims

    async def _credential(self, request: httpx.Request) -> dict[str, Any] | str | None:
        """The caller's credential, by mode: claims for an assertion, None when it passes, or a reason."""
        if self.auth == "none":
            return None
        if self.auth == "jwt_assertion":
            return await self._verify_assertion(_bearer(request))
        if self.auth == "oauth_client_credentials":
            expiry = self._issued.get(_bearer(request))
            return None if expiry is not None and expiry > time.monotonic() else "unknown or expired token"
        if self.auth == "header":
            given, expected = request.headers.get(self.auth_header, ""), self.token
        else:
            given, expected = request.headers.get("authorization", ""), f"Bearer {self.token}"
        if not given:
            return "missing credential"
        return None if self.token and _same(given, expected) else "wrong credential"

    async def _verify_assertion(self, assertion: str) -> dict[str, Any] | str:
        """The claims of an ES256 assertion signed by a key of the JWKS, or why it is refused."""
        if not assertion:
            return "missing assertion"
        try:
            kid = jwt.get_unverified_header(assertion).get("kid")
        except jwt.InvalidTokenError:
            return "malformed assertion"
        key = await self._key(kid) if isinstance(kid, str) and kid else None
        if key is None:
            return f"no key {kid!r} in the JWKS"
        try:
            claims: dict[str, Any] = jwt.decode(
                assertion,
                key,
                algorithms=["ES256"],
                audience=self.audience or None,
                issuer=self.issuer or None,
                options={"require": REQUIRED_CLAIMS, "verify_aud": bool(self.audience)},
            )
        except jwt.ExpiredSignatureError:
            return "expired assertion"
        except jwt.InvalidAudienceError:
            return "assertion for another audience"
        except jwt.InvalidTokenError as error:
            return f"invalid assertion: {error}"
        if claims["exp"] - claims["iat"] > MAX_ASSERTION_LIFETIME_S:
            return f"assertion lives longer than {MAX_ASSERTION_LIFETIME_S}s"
        jti = str(claims["jti"])
        if jti in self._seen_jti:
            return "replayed assertion"
        self._seen_jti.add(jti)
        return claims

    async def _key(self, kid: str) -> Any:
        """The public key `kid` names: in `jwks`, else in the fetched JWKS, fetched again ONCE
        when the kid is unknown (the signer rotated its key)."""
        if self.jwks is not None:
            key = _key_in(self.jwks, kid)
            if key is not None or not self.jwks_url:
                return key
        if not self.jwks_url:
            return None
        fetched_now = self._fetched_jwks is None
        if self._fetched_jwks is None:
            self._fetched_jwks = await self._fetch_jwks()
        key = _key_in(self._fetched_jwks, kid)
        if key is None and not fetched_now:
            self._fetched_jwks = await self._fetch_jwks()
            key = _key_in(self._fetched_jwks, kid)
        return key

    async def _fetch_jwks(self) -> dict[str, Any]:
        async with httpx.AsyncClient(transport=self.jwks_transport, timeout=_UPSTREAM_TIMEOUT_S) as http:
            try:
                response = await http.get(self.jwks_url)
                response.raise_for_status()
                document = response.json()
            except (httpx.HTTPError, ValueError):
                return {"keys": []}
        return document if isinstance(document, dict) else {"keys": []}

    # ── forwarding ──────────────────────────────────────────────────────────────────────────────

    async def _forward(
        self, name: str, upstream: GatewayUpstream, request: httpx.Request, body: dict[str, Any]
    ) -> httpx.Response:
        headers = self._upstream_headers(request, upstream)
        if body.get("method") == "initialize":
            return await self._initialize(name, upstream, request, body, headers)
        session = request.headers.get("mcp-session-id")
        if self.stateless:
            upstream_session = await self._stateless_session(name, upstream, request, headers)
        else:
            upstream_session = self._sessions[session][1] if session else None
        if upstream_session:
            headers["Mcp-Session-Id"] = upstream_session
        response = await _send(upstream, request.url, headers, body)
        keep = session if response.headers.get("mcp-session-id") and not self.stateless else None
        return _answer(response, keep)

    def _upstream_headers(self, request: httpx.Request, upstream: GatewayUpstream) -> dict[str, str]:
        """The caller's headers, without its credentials, with the gateway's own upstream credential."""
        dropped = {*_HOP_HEADERS, "authorization", self.auth_header.lower()}
        if self.identity_header:
            dropped.add(self.identity_header.lower())
        headers = {key: value for key, value in request.headers.items() if key.lower() not in dropped}
        headers["Content-Type"] = "application/json"
        if upstream.token:
            headers["Authorization"] = f"Bearer {upstream.token}"
        return headers

    async def _initialize(
        self,
        name: str,
        upstream: GatewayUpstream,
        request: httpx.Request,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        response = await _send(upstream, request.url, headers, body)
        upstream_session = response.headers.get("mcp-session-id")
        if response.status_code >= 400:
            return _answer(response, None)
        if self.stateless:
            self._stateless_upstream[name] = upstream_session
            return _answer(response, None)
        self._counter += 1
        session = f"gw-{name}-{self._counter}"
        self._sessions[session] = (name, upstream_session)
        answer = _answer(response, session)
        if self.forget_after_initialize > 0:
            self.forget_after_initialize -= 1
            del self._sessions[session]
        return answer

    async def _stateless_session(
        self, name: str, upstream: GatewayUpstream, request: httpx.Request, headers: dict[str, str]
    ) -> str | None:
        """The one upstream session a stateless gateway keeps per server, opened on first use."""
        if name not in self._stateless_upstream:
            version = request.headers.get("mcp-protocol-version", VERSIONS[0])
            opening = {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "fake-mcp-gateway", "version": "1"},
                },
            }
            response = await _send(upstream, request.url, headers, opening)
            if response.status_code >= 400:
                return None  # not kept: the forwarded request shows the upstream's refusal
            session = response.headers.get("mcp-session-id")
            self._stateless_upstream[name] = session
            initialized = {**headers, **({"Mcp-Session-Id": session} if session else {})}
            await _send(
                upstream, request.url, initialized, {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
        return self._stateless_upstream[name]


def _same(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def _bearer(request: httpx.Request) -> str:
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    return credential.strip() if scheme.lower() == "bearer" else ""


def _json_body(request: httpx.Request) -> dict[str, Any] | None:
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _key_in(jwks: dict[str, Any], kid: str) -> Any:
    for jwk in jwks.get("keys") or []:
        if isinstance(jwk, dict) and jwk.get("kid") == kid:
            try:
                return jwt.PyJWK(jwk).key
            except jwt.PyJWKError:
                return None
    return None


async def _send(
    upstream: GatewayUpstream, url: httpx.URL, headers: dict[str, str], body: dict[str, Any]
) -> httpx.Response:
    """One request to the upstream: its fake in memory, or its URL over HTTP."""
    content = json.dumps(body).encode("utf-8")
    if upstream.server is not None:
        request = httpx.Request("POST", url, headers=headers, content=content)
        response = await upstream.server.transport().handle_async_request(request)
        await response.aread()
        return response
    if not upstream.url:
        return httpx.Response(502, json={"error": "the upstream has neither a server nor a url"})
    try:
        async with _upstream_client() as http:
            return await http.post(upstream.url, headers=headers, content=content)
    except httpx.HTTPError as error:
        return httpx.Response(502, json={"error": f"the upstream is unreachable: {type(error).__name__}"})


def _upstream_client() -> httpx.AsyncClient:
    """The HTTP client of an upstream reached by URL; a seam the tests replace."""
    return httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT_S)


def _answer(response: httpx.Response, session: str | None) -> httpx.Response:
    """The upstream's status, body and content type, with the gateway's session instead of its own."""
    headers: dict[str, str] = {}
    content_type = response.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type
    if session:
        headers["Mcp-Session-Id"] = session
    return httpx.Response(response.status_code, content=response.content, headers=headers)


class FakeMcpGatewayApp:
    """A `FakeMcpGateway` as a bare ASGI application, which `uvicorn` serves (no framework)."""

    def __init__(self, gateway: FakeMcpGateway) -> None:
        self.gateway = gateway

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await _lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        request = httpx.Request(
            scope["method"],
            httpx.URL(path=scope["path"], query=scope.get("query_string", b"")).copy_with(
                scheme="http", host="gateway"
            ),
            headers=[(key.decode("latin-1"), value.decode("latin-1")) for key, value in scope["headers"]],
            content=body,
        )
        response = await self.gateway.respond(request)
        content = response.content
        headers = [
            (key.encode("latin-1"), value.encode("latin-1"))
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "transfer-encoding"}
        ]
        headers.append((b"content-length", str(len(content)).encode()))
        await send({"type": "http.response.start", "status": response.status_code, "headers": headers})
        await send({"type": "http.response.body", "body": content})


async def _lifespan(receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


def _choice(variable: str, default: str, allowed: tuple[str, ...]) -> str:
    value = os.environ.get(variable, "") or default
    if value not in allowed:
        raise ValueError(f"{variable}={value!r}: one of {', '.join(allowed)}")
    return value


def application() -> FakeMcpGatewayApp:
    """The factory `uvicorn --factory` calls: the gateway the `CHOREGOS_FAKE_GATEWAY_*` variables describe.

    `CHOREGOS_FAKE_GATEWAY_UPSTREAMS` is a JSON object `{server: {"url": ..., "token": ...}}`."""
    declared = json.loads(os.environ.get("CHOREGOS_FAKE_GATEWAY_UPSTREAMS", "") or "{}")
    if not isinstance(declared, dict):
        raise ValueError("CHOREGOS_FAKE_GATEWAY_UPSTREAMS is a JSON object {server: {url, token}}")
    upstreams = {
        str(name): GatewayUpstream(url=str(spec["url"]), token=str(spec.get("token", "")))
        for name, spec in declared.items()
    }
    gateway = FakeMcpGateway(
        upstreams=upstreams,
        routing=cast(Routing, _choice("CHOREGOS_FAKE_GATEWAY_ROUTING", "path", ROUTINGS)),
        server_header=os.environ.get("CHOREGOS_FAKE_GATEWAY_SERVER_HEADER", "") or "X-MCP-Server",
        auth=cast(AuthMode, _choice("CHOREGOS_FAKE_GATEWAY_AUTH", "bearer", AUTH_MODES)),
        token=os.environ.get("CHOREGOS_FAKE_GATEWAY_TOKEN", ""),
        auth_header=os.environ.get("CHOREGOS_FAKE_GATEWAY_AUTH_HEADER", "") or "X-Api-Key",
        client_id=os.environ.get("CHOREGOS_FAKE_GATEWAY_CLIENT_ID", ""),
        client_secret=os.environ.get("CHOREGOS_FAKE_GATEWAY_CLIENT_SECRET", ""),
        jwks_url=os.environ.get("CHOREGOS_FAKE_GATEWAY_JWKS_URL", ""),
        audience=os.environ.get("CHOREGOS_FAKE_GATEWAY_AUDIENCE", ""),
        issuer=os.environ.get("CHOREGOS_FAKE_GATEWAY_ISSUER", ""),
    )
    return FakeMcpGatewayApp(gateway)


if __name__ == "__main__":  # pragma: no cover - the container's entry point
    import uvicorn

    uvicorn.run(application(), host="0.0.0.0", port=int(os.environ.get("CHOREGOS_FAKE_GATEWAY_PORT", "8091")))
