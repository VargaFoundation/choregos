# SPDX-License-Identifier: Apache-2.0
"""The fake MCP gateway (S46-04, ADR 0005, ADR 0105): it routes by path or by header, checks every
auth mode Choregos speaks, holds the upstream credentials, records who called, forgets sessions as
another replica would, and is served alone as an ASGI application.

`FakeMcpServer`'s fields are French until S45 renames them: they are read in `_server()` and
`_received()` only, so the rename touches two lines here.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import choregos_adapters.fakes.mcp_gateway as gateway_module
import httpx
import jwt
import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.mcp_gateway import (
    FakeMcpGateway,
    FakeMcpGatewayApp,
    GatewayUpstream,
    application,
)
from choregos_adapters.mcp import McpClient, McpError
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

SUPPLIER_TOOLS = [
    {"name": "order_laptop", "inputSchema": {"type": "object"}},
    {"name": "track_order", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
]
MDM_TOOLS = [{"name": "enroll_device", "inputSchema": {"type": "object"}}]
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
AUDIENCE = "https://gw.test"


def _server(
    tools: list[dict[str, Any]],
    token: str = "",
    gestures: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
) -> FakeMcpServer:
    return FakeMcpServer(outils=list(tools), jeton=token, gestes=dict(gestures or {}))


def _received(server: FakeMcpServer) -> list[tuple[str, dict[str, str], dict[str, Any]]]:
    return server.recues


def _methods(server: FakeMcpServer) -> list[str]:
    return [method for method, _, _ in _received(server)]


def _names(tools: list[Any]) -> list[str]:
    return [tool.name if hasattr(tool, "name") else tool["name"] for tool in tools]


async def _post(
    gateway: FakeMcpGateway, path: str, body: dict[str, Any], headers: dict[str, str] | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(transport=gateway.transport(), base_url="https://gw.test") as http:
        return await http.post(path, json=body, headers=headers or {})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _key_pair(kid: str) -> tuple[ec.EllipticCurvePrivateKey, dict[str, Any]]:
    private = ec.generate_private_key(ec.SECP256R1())
    return private, {**ECAlgorithm.to_jwk(private.public_key(), as_dict=True), "kid": kid}


def _assertion(
    private: ec.EllipticCurvePrivateKey,
    kid: str,
    *,
    sub: str = "agent-7",
    aud: str = AUDIENCE,
    iat: int | None = None,
    lifetime: int = 60,
) -> str:
    issued = int(time.time()) if iat is None else iat
    claims = {"sub": sub, "iat": issued, "exp": issued + lifetime, "jti": uuid.uuid4().hex, "aud": aud}
    return jwt.encode(claims, private, algorithm="ES256", headers={"kid": kid})


async def test_path_routing_reaches_the_named_server_and_404s_an_unknown_one() -> None:
    supplier, mdm = _server(SUPPLIER_TOOLS), _server(MDM_TOOLS)
    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier), "mdm": GatewayUpstream(server=mdm)},
        token="gw",
    )
    tools = await McpClient("https://gw.test/supplier/mcp", token="gw", transport=gw.transport()).list_tools()
    assert _names(tools) == _names(SUPPLIER_TOOLS)
    assert _methods(mdm) == [], "the other server saw nothing"
    assert {call.server for call in gw.calls} == {"supplier"}
    with pytest.raises(McpError) as unknown:
        await McpClient("https://gw.test/other/mcp", token="gw", transport=gw.transport()).list_tools()
    assert unknown.value.code == 404


async def test_header_routing_reads_the_server_header() -> None:
    supplier, mdm = _server(SUPPLIER_TOOLS), _server(MDM_TOOLS)
    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier), "mdm": GatewayUpstream(server=mdm)},
        routing="header",
        token="gw",
    )
    headers = {**_bearer("gw"), "X-MCP-Server": "supplier"}
    opened = await _post(gw, "/mcp", INITIALIZE, headers)
    assert opened.status_code == 200
    listed = await _post(
        gw, "/mcp", TOOLS_LIST, {**headers, "Mcp-Session-Id": opened.headers["mcp-session-id"]}
    )
    assert _names(listed.json()["result"]["tools"]) == _names(SUPPLIER_TOOLS)
    assert _methods(mdm) == []
    assert gw.calls[-1].headers["x-mcp-server"] == "supplier"
    elsewhere = await _post(gw, "/mcp", INITIALIZE, {**_bearer("gw"), "X-MCP-Server": "other"})
    assert elsewhere.status_code == 404
    assert (await _post(gw, "/mcp", INITIALIZE, _bearer("gw"))).status_code == 404, "no header, no server"


@pytest.mark.parametrize(
    ("auth", "auth_header", "seen"),
    [("bearer", "Authorization", "authorization"), ("header", "X-Api-Key", "x-api-key")],
)
async def test_the_upstream_gets_the_gateway_s_credential_never_choregos_s(
    auth: Any, auth_header: str, seen: str
) -> None:
    supplier = _server(SUPPLIER_TOOLS, token="upstream-key")
    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier, token="upstream-key")},
        auth=auth,
        token="gw-secret",
    )
    client = McpClient(
        "https://gw.test/supplier/mcp", token="gw-secret", auth_header=auth_header, transport=gw.transport()
    )
    assert _names(await client.list_tools()) == _names(SUPPLIER_TOOLS)
    received = _received(supplier)
    assert received
    assert all(headers.get("authorization") == "Bearer upstream-key" for _, headers, _ in received)
    assert all("x-api-key" not in headers for _, headers, _ in received), "the API-key header is dropped"
    assert "gw-secret" not in json.dumps(received), "Choregos's credential never reaches the upstream"
    assert gw.calls[0].headers[seen] == ("Bearer gw-secret" if auth == "bearer" else "gw-secret")


async def test_the_upstream_sees_exactly_one_content_type() -> None:
    supplier = _server(SUPPLIER_TOOLS)
    gw = FakeMcpGateway(upstreams={"supplier": GatewayUpstream(server=supplier)}, auth="none")
    client = McpClient("https://gw.test/supplier/mcp", transport=gw.transport())
    assert _names(await client.list_tools()) == _names(SUPPLIER_TOOLS)
    assert gw.calls[0].headers["content-type"] == "application/json", "the caller did send one"
    received = _received(supplier)
    assert received
    # Two headers of one name reach the upstream as one value joined by ", ".
    assert {headers["content-type"] for _, headers, _ in received} == {"application/json"}


@pytest.mark.parametrize(
    ("auth", "good", "bad"),
    [
        ("bearer", _bearer("gw"), [_bearer("nope"), {}, {"Authorization": "gw"}, {"X-Api-Key": "gw"}]),
        ("header", {"X-Api-Key": "gw"}, [{"X-Api-Key": "nope"}, {}, _bearer("gw")]),
    ],
)
async def test_a_wrong_credential_is_a_401(
    auth: Any, good: dict[str, str], bad: list[dict[str, str]]
) -> None:
    supplier = _server(SUPPLIER_TOOLS)
    gw = FakeMcpGateway(upstreams={"supplier": GatewayUpstream(server=supplier)}, auth=auth, token="gw")
    assert (await _post(gw, "/supplier/mcp", INITIALIZE, good)).status_code == 200
    for headers in bad:
        refused = await _post(gw, "/supplier/mcp", INITIALIZE, headers)
        assert refused.status_code == 401, headers
        assert refused.headers["www-authenticate"] == "Bearer"
    assert len(gw.refusals) == len(bad)
    assert {status for status, _ in gw.refusals} == {401}
    assert _methods(supplier) == ["initialize"], "a refused request never reaches the upstream"


async def test_a_gateway_without_a_token_refuses_every_bearer() -> None:
    gw = FakeMcpGateway(upstreams={"supplier": GatewayUpstream(server=_server(SUPPLIER_TOOLS))}, token="")
    for headers in (_bearer(""), _bearer("anything"), {}):
        assert (await _post(gw, "/supplier/mcp", INITIALIZE, headers)).status_code == 401


async def _issue(http: httpx.AsyncClient, secret: str = "s3cret", **form: str) -> httpx.Response:
    data = {"grant_type": "client_credentials", **form}
    return await http.post("/oauth/token", data=data, auth=("choregos", secret))


async def test_client_credentials_issue_a_token_that_opens_the_gateway() -> None:
    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=_server(SUPPLIER_TOOLS))},
        auth="oauth_client_credentials",
        client_id="choregos",
        client_secret="s3cret",
    )
    async with httpx.AsyncClient(transport=gw.transport(), base_url="https://gw.test") as http:
        issued = await _issue(http, resource="https://gw.test", scope="mcp")
        assert issued.status_code == 200
        assert issued.json()["token_type"] == "Bearer"
        assert issued.json()["expires_in"] == 300
        assert gw.token_requests == [{"scope": "mcp", "resource": "https://gw.test", "audience": ""}]
        token = issued.json()["access_token"]
        client = McpClient("https://gw.test/supplier/mcp", token=token, transport=gw.transport())
        assert _names(await client.list_tools()) == _names(SUPPLIER_TOOLS)
        bad_secret = await _issue(http, secret="wrong", resource="https://gw.test")
        assert bad_secret.status_code == 401
        assert bad_secret.json() == {"error": "invalid_client"}
        wrong_grant = await http.post(
            "/oauth/token", data={"grant_type": "password"}, auth=("choregos", "s3cret")
        )
        assert wrong_grant.status_code == 400
        assert len(gw.token_requests) == 1, "a refused request issues nothing"
    with pytest.raises(McpError) as made_up:
        await McpClient(
            "https://gw.test/supplier/mcp", token="made-up", transport=gw.transport()
        ).list_tools()
    assert made_up.value.code == 401


async def test_revoked_tokens_no_longer_open_the_gateway() -> None:
    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=_server(SUPPLIER_TOOLS))},
        auth="oauth_client_credentials",
        client_id="choregos",
        client_secret="s3cret",
    )
    async with httpx.AsyncClient(transport=gw.transport(), base_url="https://gw.test") as http:
        token = (await _issue(http)).json()["access_token"]
    client = McpClient("https://gw.test/supplier/mcp", token=token, transport=gw.transport())
    assert _names(await client.list_tools()) == _names(SUPPLIER_TOOLS)
    gw.revoke_tokens()
    with pytest.raises(McpError) as revoked:
        await McpClient("https://gw.test/supplier/mcp", token=token, transport=gw.transport()).list_tools()
    assert revoked.value.code == 401
    assert gw.refusals[-1] == (401, "unknown or expired token")


async def test_an_expired_token_no_longer_opens_the_gateway() -> None:
    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=_server(SUPPLIER_TOOLS))},
        auth="oauth_client_credentials",
        client_id="choregos",
        client_secret="s3cret",
        token_ttl_s=0,
    )
    async with httpx.AsyncClient(transport=gw.transport(), base_url="https://gw.test") as http:
        token = (await _issue(http)).json()["access_token"]
    assert (await _post(gw, "/supplier/mcp", INITIALIZE, _bearer(token))).status_code == 401


async def test_a_signed_assertion_is_verified_against_the_jwks() -> None:
    private, jwk = _key_pair("kid1")
    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=_server(SUPPLIER_TOOLS))},
        auth="jwt_assertion",
        jwks={"keys": [jwk]},
        audience=AUDIENCE,
    )
    path = "/supplier/mcp"
    opened = await _post(gw, path, INITIALIZE, _bearer(_assertion(private, "kid1", sub="lea@acme.test")))
    assert opened.status_code == 200
    session = {"Mcp-Session-Id": opened.headers["mcp-session-id"]}
    listed = await _post(
        gw, path, TOOLS_LIST, {**_bearer(_assertion(private, "kid1", sub="lea@acme.test")), **session}
    )
    assert _names(listed.json()["result"]["tools"]) == _names(SUPPLIER_TOOLS)
    assert gw.calls[-1].method == "tools/list"
    assert gw.calls[-1].claims is not None
    assert gw.calls[-1].claims["sub"] == "lea@acme.test"
    impostor, _ = _key_pair("kid1")
    too_long = _assertion(private, "kid1", lifetime=600)
    for refused in (
        _assertion(impostor, "kid1"),
        _assertion(private, "kid-unknown"),
        too_long,
        "not-a-jwt",
        "",
    ):
        assert (await _post(gw, path, INITIALIZE, _bearer(refused))).status_code == 401
    assert len(gw.refusals) == 5


async def test_an_assertion_that_is_expired_replayed_or_for_another_audience_is_refused() -> None:
    private, jwk = _key_pair("kid1")
    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=_server(SUPPLIER_TOOLS))},
        auth="jwt_assertion",
        jwks={"keys": [jwk]},
        audience=AUDIENCE,
    )
    now = int(time.time())
    expired = _assertion(private, "kid1", iat=now - 200, lifetime=100)
    replayed = _assertion(private, "kid1")
    elsewhere = _assertion(private, "kid1", aud="https://elsewhere.test")
    assert (await _post(gw, "/supplier/mcp", INITIALIZE, _bearer(replayed))).status_code == 200
    statuses = [
        (await _post(gw, "/supplier/mcp", INITIALIZE, _bearer(assertion))).status_code
        for assertion in (expired, replayed, elsewhere)
    ]
    assert statuses == [401, 401, 401]
    assert gw.refusals == [
        (401, "expired assertion"),
        (401, "replayed assertion"),
        (401, "assertion for another audience"),
    ]


async def test_an_assertion_from_another_issuer_or_without_sub_or_jti_is_refused() -> None:
    private, jwk = _key_pair("kid1")
    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=_server(SUPPLIER_TOOLS))},
        auth="jwt_assertion",
        jwks={"keys": [jwk]},
        audience=AUDIENCE,
        issuer="https://idp.test",
    )

    def signed(**overrides: Any) -> str:
        issued = int(time.time())
        claims = {
            "iss": "https://idp.test",
            "sub": "agent-7",
            "aud": AUDIENCE,
            "iat": issued,
            "exp": issued + 60,
            "jti": uuid.uuid4().hex,
            **overrides,
        }
        kept = {key: value for key, value in claims.items() if value is not None}
        return jwt.encode(kept, private, algorithm="ES256", headers={"kid": "kid1"})

    assert (await _post(gw, "/supplier/mcp", INITIALIZE, _bearer(signed()))).status_code == 200
    refused = [signed(iss="https://other-idp.test"), signed(sub=None), signed(jti=None)]
    statuses = [
        (await _post(gw, "/supplier/mcp", INITIALIZE, _bearer(assertion))).status_code
        for assertion in refused
    ]
    assert statuses == [401, 401, 401]
    assert [status for status, _ in gw.refusals] == [401, 401, 401]
    assert "iss" in gw.refusals[0][1]
    assert "sub" in gw.refusals[1][1]
    assert "jti" in gw.refusals[2][1]


async def test_a_rotated_key_is_fetched_again_from_the_jwks_url() -> None:
    first, first_jwk = _key_pair("kid1")
    second, second_jwk = _key_pair("kid2")
    published: dict[str, Any] = {"keys": [first_jwk]}
    fetches: list[str] = []

    def jwks(request: httpx.Request) -> httpx.Response:
        fetches.append(str(request.url))
        return httpx.Response(200, json=published)

    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=_server(SUPPLIER_TOOLS))},
        auth="jwt_assertion",
        jwks_url="https://idp.test/.well-known/jwks.json",
        jwks_transport=httpx.MockTransport(jwks),
        audience=AUDIENCE,
    )
    assert (
        await _post(gw, "/supplier/mcp", INITIALIZE, _bearer(_assertion(first, "kid1")))
    ).status_code == 200
    published["keys"] = [second_jwk]
    assert (
        await _post(gw, "/supplier/mcp", INITIALIZE, _bearer(_assertion(second, "kid2")))
    ).status_code == 200
    assert fetches == ["https://idp.test/.well-known/jwks.json"] * 2
    assert (
        await _post(gw, "/supplier/mcp", INITIALIZE, _bearer(_assertion(second, "kid2")))
    ).status_code == 200
    assert len(fetches) == 2, "a known kid is read from the cache"


async def test_an_identity_header_must_be_a_signed_assertion() -> None:
    private, jwk = _key_pair("kid1")
    supplier = _server(SUPPLIER_TOOLS)
    gw = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier)},
        auth="none",
        identity_header="X-Choregos-Identity",
        jwks={"keys": [jwk]},
    )
    signed = _assertion(private, "kid1", sub="lea@acme.test")
    assert (await _post(gw, "/supplier/mcp", INITIALIZE, {"X-Choregos-Identity": signed})).status_code == 200
    assert gw.calls[-1].identity_header_claims is not None
    assert gw.calls[-1].identity_header_claims["sub"] == "lea@acme.test"
    issued = int(time.time())
    claims = {"sub": "lea@acme.test", "iat": issued, "exp": issued + 60, "jti": uuid.uuid4().hex}
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": "kid1"})
    for header in ({"X-Choregos-Identity": unsigned}, {"X-Choregos-Identity": "lea@acme.test"}, {}):
        assert (await _post(gw, "/supplier/mcp", INITIALIZE, header)).status_code == 401
    assert len(gw.refusals) == 3
    assert all("x-choregos-identity" not in headers for _, headers, _ in _received(supplier))


async def test_an_unknown_or_forgotten_gateway_session_is_a_404() -> None:
    gw = FakeMcpGateway(upstreams={"supplier": GatewayUpstream(server=_server(SUPPLIER_TOOLS))}, auth="none")
    unknown = await _post(gw, "/supplier/mcp", TOOLS_LIST, {"Mcp-Session-Id": "gw-nope"})
    assert unknown.status_code == 404
    assert unknown.json() == {"error": "unknown session"}
    assert gw.refusals == [(404, "unknown session")]

    supplier = _server(SUPPLIER_TOOLS)
    forgetful = FakeMcpGateway(
        upstreams={
            "supplier": GatewayUpstream(server=supplier),
            "mdm": GatewayUpstream(server=_server(MDM_TOOLS)),
        },
        auth="none",
        forget_after_initialize=1,
    )
    opened = await _post(forgetful, "/supplier/mcp", INITIALIZE)
    assert opened.headers["mcp-session-id"] == "gw-supplier-1", "the gateway's own id, not the upstream's"
    forgotten = await _post(forgetful, "/supplier/mcp", TOOLS_LIST, {"Mcp-Session-Id": "gw-supplier-1"})
    assert forgotten.status_code == 404
    assert forgetful.refusals == [(404, "unknown session")]
    kept = (await _post(forgetful, "/supplier/mcp", INITIALIZE)).headers["mcp-session-id"]
    listed = await _post(forgetful, "/supplier/mcp", TOOLS_LIST, {"Mcp-Session-Id": kept})
    assert _names(listed.json()["result"]["tools"]) == _names(SUPPLIER_TOOLS), "only one session is forgotten"
    assert (await _post(forgetful, "/mdm/mcp", TOOLS_LIST, {"Mcp-Session-Id": kept})).status_code == 404
    assert _methods(supplier).count("tools/list") == 1


async def test_a_stateless_gateway_answers_without_a_session() -> None:
    supplier = _server(SUPPLIER_TOOLS)
    gw = FakeMcpGateway(upstreams={"supplier": GatewayUpstream(server=supplier)}, auth="none", stateless=True)
    answers = [
        await _post(gw, "/supplier/mcp", TOOLS_LIST),
        await _post(gw, "/supplier/mcp", {**TOOLS_LIST, "id": 3}),
    ]
    for answer in answers:
        assert answer.status_code == 200
        assert "mcp-session-id" not in answer.headers
        assert _names(answer.json()["result"]["tools"]) == _names(SUPPLIER_TOOLS)
    assert _methods(supplier).count("initialize") == 1, "the upstream is initialised once"
    assert _methods(supplier).count("tools/list") == 2
    opened = await _post(gw, "/supplier/mcp", INITIALIZE)
    assert opened.status_code == 200
    assert "mcp-session-id" not in opened.headers
    client = McpClient("https://gw.test/supplier/mcp", transport=gw.transport())
    assert _names(await client.list_tools()) == _names(SUPPLIER_TOOLS)


async def test_the_fallback_is_resolved_on_every_request() -> None:
    first, second = _server(SUPPLIER_TOOLS), _server(MDM_TOOLS)
    current = {"server": first}
    gw = FakeMcpGateway(fallback=lambda: current["server"], auth="none")

    def client() -> McpClient:
        return McpClient("https://gw.test/unknown/mcp", transport=gw.transport())

    assert _names(await client().list_tools()) == _names(SUPPLIER_TOOLS)
    current["server"] = second
    assert _names(await client().list_tools()) == _names(MDM_TOOLS)
    assert "tools/list" in _methods(second)
    assert _methods(first).count("tools/list") == 1, "the first server saw nothing after the switch"


async def test_the_asgi_app_serves_health_and_mcp() -> None:
    gw = FakeMcpGateway(upstreams={"supplier": GatewayUpstream(server=_server(SUPPLIER_TOOLS))}, token="gw")
    app = FakeMcpGatewayApp(gw)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as http:
        health = await http.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"ok": True}
    client = McpClient("http://gateway/supplier/mcp", token="gw", transport=httpx.ASGITransport(app=app))
    assert _names(await client.list_tools()) == _names(SUPPLIER_TOOLS)

    messages = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent: list[str] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message["type"])

    await app({"type": "lifespan"}, receive, send)
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


def test_application_reads_its_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    upstreams = {"supplier": {"url": "http://choregos-demo-fakes:8090/supplier/mcp", "token": "demo-token"}}
    monkeypatch.setenv("CHOREGOS_FAKE_GATEWAY_UPSTREAMS", json.dumps(upstreams))
    monkeypatch.setenv("CHOREGOS_FAKE_GATEWAY_AUTH", "header")
    monkeypatch.setenv("CHOREGOS_FAKE_GATEWAY_TOKEN", "gw-key")
    monkeypatch.setenv("CHOREGOS_FAKE_GATEWAY_AUTH_HEADER", "X-Gateway-Key")
    monkeypatch.delenv("CHOREGOS_FAKE_GATEWAY_ROUTING", raising=False)
    gw = application().gateway
    assert gw.upstreams["supplier"].url == "http://choregos-demo-fakes:8090/supplier/mcp"
    assert gw.upstreams["supplier"].token == "demo-token"
    assert gw.upstreams["supplier"].server is None
    assert (gw.auth, gw.token, gw.auth_header, gw.routing) == ("header", "gw-key", "X-Gateway-Key", "path")
    monkeypatch.setenv("CHOREGOS_FAKE_GATEWAY_AUTH", "basic")
    with pytest.raises(ValueError, match="CHOREGOS_FAKE_GATEWAY_AUTH"):
        application()


async def test_an_http_upstream_gets_the_gateway_s_token(monkeypatch: pytest.MonkeyPatch) -> None:
    demo = _server(SUPPLIER_TOOLS, token="demo-token")
    monkeypatch.setattr(
        gateway_module, "_upstream_client", lambda: httpx.AsyncClient(transport=demo.transport())
    )
    gw = FakeMcpGateway(
        upstreams={
            "supplier": GatewayUpstream(
                url="http://choregos-demo-fakes:8090/supplier/mcp", token="demo-token"
            )
        },
        token="gw",
    )
    client = McpClient("https://gw.test/supplier/mcp", token="gw", transport=gw.transport())
    assert _names(await client.list_tools()) == _names(SUPPLIER_TOOLS)
    assert {headers["authorization"] for _, headers, _ in _received(demo)} == {"Bearer demo-token"}

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(
        gateway_module,
        "_upstream_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(unreachable)),
    )
    assert (await _post(gw, "/supplier/mcp", INITIALIZE, _bearer("gw"))).status_code == 502
