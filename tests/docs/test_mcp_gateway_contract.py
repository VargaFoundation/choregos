# SPDX-License-Identifier: Apache-2.0
"""An organisation's MCP gateway contract (ADR 0105, S46-06): shape, write-only secret, 409 codes.

Contract only: the API does not serve these routes yet (`CONTRACT_ONLY` in
`apps/api/tests/test_contract_conformance.py`), so what is proven here is what the document says.
"""

from __future__ import annotations

import pathlib
from typing import Any

import jsonschema
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC: dict[str, Any] = yaml.safe_load((ROOT / "packages" / "contracts" / "openapi.yaml").read_text())
SCHEMAS = SPEC["components"]["schemas"]
PATHS = SPEC["paths"]
CODES = ["gateway_in_use", "gateway_required", "gateway_not_configured", "gateway_assertion_key_missing"]


def _validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"components": SPEC["components"], **SCHEMAS[name]})


def _put(**over: Any) -> dict[str, Any]:
    base = {
        "mode": "available",
        "endpoint": "https://gw.test/{server}/mcp",
        "routing": "path",
        "auth": {"type": "bearer"},
    }
    return base | over


def _ops() -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, verb): op
        for path, item in PATHS.items()
        if "mcp-gateway" in path and path.startswith("/orgs/")
        for verb, op in item.items()
        if verb in {"get", "put", "delete", "post"}
    }


def test_the_six_operations_and_their_routes() -> None:
    assert {key: op["operationId"] for key, op in _ops().items()} == {
        ("/orgs/{org}/mcp-gateway", "get"): "getMcpGateway",
        ("/orgs/{org}/mcp-gateway", "put"): "putMcpGateway",
        ("/orgs/{org}/mcp-gateway", "delete"): "deleteMcpGateway",
        ("/orgs/{org}/mcp-gateway/test", "post"): "testMcpGateway",
        ("/orgs/{org}/mcp-gateway/assertion-key", "post"): "rotateMcpGatewayAssertionKey",
        ("/orgs/{org}/mcp-gateway/jwks.json", "get"): "getMcpGatewayJwks",
    }


def test_only_the_jwks_is_public() -> None:
    for (_, _), op in _ops().items():
        if op["operationId"] == "getMcpGatewayJwks":
            assert op["security"] == []
        else:
            assert "security" not in op, op["operationId"]


@pytest.mark.parametrize(
    "name",
    [
        "McpGateway",
        "McpGatewayAuth",
        "McpGatewayState",
        "McpGatewayPut",
        "McpGatewayTest",
        "McpGatewayTestRequest",
        "McpGatewayAssertion",
        "McpGatewayAssertionKey",
    ],
)
def test_schemas_are_closed(name: str) -> None:
    assert SCHEMAS[name]["additionalProperties"] is False


def test_the_secret_is_write_only_and_never_in_an_answer() -> None:
    assert SCHEMAS["McpGatewayPut"]["properties"]["secret"]["writeOnly"] is True
    assert "secret" not in SCHEMAS["McpGatewayPut"]["required"]
    for name in ("McpGateway", "McpGatewayState", "McpGatewayTest", "McpGatewayAssertion"):
        assert "secret" not in SCHEMAS[name]["properties"], name
    assert SCHEMAS["McpGateway"]["properties"]["secret_set"] == {"type": "boolean"}
    assert not list(_validator("McpGatewayPut").iter_errors(_put()))  # absent keeps the stored one
    assert not list(_validator("McpGatewayPut").iter_errors(_put(secret="s3cret")))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://u:p@gw.test/{server}/mcp",
        "https://gw.test/mcp?x=1",
        "https://gw.test/mcp#f",
        "ftp://gw.test",
    ],
)
def test_the_endpoint_carries_no_credentials_query_or_fragment(endpoint: str) -> None:
    v = _validator("McpGatewayPut")
    assert list(v.iter_errors(_put(endpoint=endpoint)))
    assert not list(v.iter_errors(_put(endpoint="https://gw.test/{server}/mcp")))


def test_modes_routing_and_auth_types_are_bounded() -> None:
    props = SCHEMAS["McpGatewayPut"]["properties"]
    assert props["mode"]["enum"] == ["off", "available", "required"]
    assert props["routing"]["enum"] == ["path", "header"]
    assert SCHEMAS["McpGatewayAuth"]["properties"]["type"]["enum"] == [
        "bearer",
        "header",
        "oauth_client_credentials",
        "jwt_assertion",
    ]
    v = _validator("McpGatewayPut")
    assert list(v.iter_errors(_put(mode="maybe")))
    assert list(v.iter_errors(_put(routing="query")))
    assert list(v.iter_errors(_put(auth={"type": "basic"})))


def _409(op_id: str) -> dict[str, Any]:
    for item in PATHS.values():
        for op in item.values():
            if isinstance(op, dict) and op.get("operationId") == op_id:
                return op["responses"]["409"]  # type: ignore[no-any-return]
    raise AssertionError(op_id)


def test_gateway_conflicts_name_their_code() -> None:
    assert SCHEMAS["McpGatewayConflict"]["properties"]["code"]["enum"] == CODES
    assert SCHEMAS["EgressRequired"]["properties"]["code"]["enum"] == ["egress_required"]
    ref = {"$ref": "#/components/responses/McpGatewayConflict"}
    assert _409("deleteMcpGateway") == ref
    assert _409("discoverConnectorOperations") == ref
    both = [
        {"$ref": "#/components/schemas/EgressRequired"},
        {"$ref": "#/components/schemas/McpGatewayConflict"},
    ]
    for op_id in ("createOrgConnector", "installMcpCatalogueEntry"):
        assert _409(op_id)["content"]["application/problem+json"]["schema"]["oneOf"] == both, op_id
    assert _409("putMcpGateway") == {"$ref": "#/components/responses/EgressRequired"}
    v = _validator("McpGatewayConflict")
    problem = {"type": "about:blank", "title": "Conflict", "status": 409, "code": "gateway_in_use"}
    assert not list(v.iter_errors(problem | {"connectors": ["github"]}))
    assert list(v.iter_errors(problem | {"code": "egress_required"}))


def test_catalogue_entries_say_their_routes_and_installs_take_one() -> None:
    entry = SCHEMAS["McpCatalogueEntry"]
    routes = entry["properties"]["install_routes"]
    assert routes["items"]["enum"] == ["direct", "gateway"]
    assert "install_routes" not in entry["required"]
    assert entry["properties"]["install"]["enum"] == ["installable", "needs_oauth", "needs_hosting"]
    install = SCHEMAS["McpCatalogueInstall"]["properties"]
    assert install["route"]["enum"] == ["direct", "gateway", None]
    assert "gateway_server" in install
    v = _validator("McpCatalogueInstall")
    assert not list(v.iter_errors({"route": "gateway", "gateway_server": "github"}))
    assert not list(v.iter_errors({"route": None}))
    assert list(v.iter_errors({"route": "proxy"}))
    assert list(v.iter_errors({"gateway_server": "-bad"}))


def test_a_problem_body_the_api_sends_matches_the_gateway_conflict_alone() -> None:
    """The API always adds `instance` to a problem; the closed schema must accept it (S46-06)."""
    from choregos_api.errors import conflict

    body = conflict("x", extra={"code": "gateway_required"}).to_problem("/api/v1/orgs/acme/connectors")
    assert body["instance"] == "/api/v1/orgs/acme/connectors"
    assert not list(_validator("McpGatewayConflict").iter_errors(body))
    branches = _409("createOrgConnector")["content"]["application/problem+json"]["schema"]["oneOf"]
    matching = [
        branch["$ref"]
        for branch in branches
        if not list(
            jsonschema.Draft202012Validator({"components": SPEC["components"], **branch}).iter_errors(body)
        )
    ]
    assert matching == ["#/components/schemas/McpGatewayConflict"]
    # `errors` and `retry_after` are members the API may add to any problem.
    fuller = body | {"errors": [{"loc": ["body"], "msg": "m"}], "retry_after": 3}
    assert not list(_validator("McpGatewayConflict").iter_errors(fuller))
