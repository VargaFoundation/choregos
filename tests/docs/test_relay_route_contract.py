# SPDX-License-Identifier: Apache-2.0
"""The outbound-proxy relay's route (ADR 0091, S40-26d-a): who may ask, and where the password goes.

Contract only: the API does not serve the route yet (`CONTRACT_ONLY` in
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
PATH = "/internal/orgs/{org}/outbound-proxy/{kind}"
KINDS = ["web_fetch", "web_search", "knowledge_web", "search_engines"]


def _validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"components": SPEC["components"], **SCHEMAS[name]})


def _creds(**over: Any) -> dict[str, Any]:
    base = {"endpoint": "gate.decodo.com:7000", "scheme": "http", "username": "u", "password": "p"}
    return base | over


def test_one_operation_a_relay_token_only() -> None:
    item = SPEC["paths"][PATH]
    assert [verb for verb in item if verb in {"get", "put", "post", "delete", "patch"}] == ["get"]
    op = item["get"]
    assert op["operationId"] == "getRelayOutboundProxy"
    assert op["security"] == [{"relayToken": []}]  # not a run token, a session or an API token
    assert op["tags"] == ["internal"]
    assert {"401", "403", "404", "422", "503"} <= set(op["responses"])


def test_the_relay_token_is_a_bearer_jwt_for_its_own_audience() -> None:
    scheme = SPEC["components"]["securitySchemes"]["relayToken"]
    assert scheme["type"] == "http" and scheme["scheme"] == "bearer" and scheme["bearerFormat"] == "JWT"
    assert "aud=choregos-relay" in scheme["description"]
    assert "internal" not in scheme["description"].replace("/internal", "")  # not the run token's audience


def test_the_kind_is_bounded_to_the_four_kinds() -> None:
    kind = next(p for p in SPEC["paths"][PATH]["parameters"] if p.get("name") == "kind")
    assert kind["schema"]["enum"] == KINDS


def test_the_answer_is_a_proxy_or_null() -> None:
    v = _validator("RelayOutboundProxy")
    assert not list(v.iter_errors({"proxy": None}))
    assert not list(v.iter_errors({"proxy": _creds()}))
    assert list(v.iter_errors({}))
    assert list(v.iter_errors({"proxy": _creds(), "extra": 1}))
    assert list(v.iter_errors({"proxy": _creds(endpoint="https://gate:7000")}))
    assert list(v.iter_errors({"proxy": _creds(scheme="socks5")}))
    assert list(v.iter_errors({"proxy": {k: v_ for k, v_ in _creds().items() if k != "password"}}))


def _refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if "$ref" in node else []
        return found + [ref for value in node.values() for ref in _refs(value)]
    if isinstance(node, list):
        return [ref for value in node for ref in _refs(value)]
    return []


def test_the_clear_password_is_served_by_this_route_and_no_other() -> None:
    """`RelayProxyCredentials` is the one answer schema with a password: only the relay route reaches it."""
    users = [
        (path, verb)
        for path, item in SPEC["paths"].items()
        for verb, op in item.items()
        if isinstance(op, dict) and "#/components/schemas/RelayOutboundProxy" in _refs(op)
    ]
    assert users == [(PATH, "get")]
    holders = [
        name
        for name, schema in SCHEMAS.items()
        if "#/components/schemas/RelayProxyCredentials" in _refs(schema)
    ]
    assert holders == ["RelayOutboundProxy"]
    # No other schema carries a password unless it is the write-only request.
    clear = sorted(
        name
        for name, schema in SCHEMAS.items()
        if "password" in schema.get("properties", {})
        and not schema["properties"]["password"].get("writeOnly")
    )
    assert clear == ["RelayProxyCredentials"]


def test_the_outbound_proxy_schemas_still_never_answer_with_a_password() -> None:
    for name in ("OutboundProxy", "OutboundProxyState", "OutboundProxyTest"):
        assert "password" not in SCHEMAS[name]["properties"], name


def test_the_route_says_it_is_not_cached_and_names_the_adr() -> None:
    description = SPEC["paths"][PATH]["get"]["description"]
    assert "no-store" in description and "ADR 0091" in description
    adr = ROOT / "docs" / "adr" / "0091-the-search-engines-reach-the-proxy-through-a-relay.md"
    assert "accepted, 2026-10-26" in adr.read_text()
    assert adr.name in (ROOT / "docs" / "adr" / "README.md").read_text()


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_is_in_the_path_enum(kind: str) -> None:
    schema = next(p for p in SPEC["paths"][PATH]["parameters"] if p.get("name") == "kind")["schema"]
    assert kind in schema["enum"]
