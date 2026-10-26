# SPDX-License-Identifier: Apache-2.0
"""The outbound proxy contract (ADR 0087, S40-26a): shape, write-only password, endpoint pattern.

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
KINDS = ["web_fetch", "web_search", "knowledge_web", "search_engines"]


def _validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"components": SPEC["components"], **SCHEMAS[name]})


def _put(**over: Any) -> dict[str, Any]:
    base = {"enabled": True, "endpoint": "gate.decodo.com:7000", "scheme": "http", "username": "user-x"}
    return base | over


def test_the_four_operations_and_their_routes() -> None:
    ops = {
        (path, verb): op["operationId"]
        for path, item in SPEC["paths"].items()
        if "outbound-proxy" in path
        for verb, op in item.items()
        if verb in {"get", "put", "delete", "post"}
    }
    assert ops == {
        ("/orgs/{org}/outbound-proxy", "get"): "getOutboundProxy",
        ("/orgs/{org}/outbound-proxy", "put"): "putOutboundProxy",
        ("/orgs/{org}/outbound-proxy", "delete"): "deleteOutboundProxy",
        ("/orgs/{org}/outbound-proxy/test", "post"): "testOutboundProxy",
    }
    assert "204" in SPEC["paths"]["/orgs/{org}/outbound-proxy"]["delete"]["responses"]


@pytest.mark.parametrize(
    "name", ["OutboundProxy", "OutboundProxyState", "OutboundProxyPut", "OutboundProxyTest"]
)
def test_schemas_are_closed(name: str) -> None:
    assert SCHEMAS[name]["additionalProperties"] is False


def test_the_password_is_write_only_and_never_in_an_answer() -> None:
    assert SCHEMAS["OutboundProxyPut"]["properties"]["password"]["writeOnly"] is True
    assert "password" not in SCHEMAS["OutboundProxyPut"]["required"]
    for name in ("OutboundProxy", "OutboundProxyState", "OutboundProxyTest"):
        assert "password" not in SCHEMAS[name]["properties"], name
    assert SCHEMAS["OutboundProxy"]["properties"]["password_set"] == {"type": "boolean"}
    assert not list(_validator("OutboundProxyPut").iter_errors(_put()))  # absent keeps the stored one
    assert not list(_validator("OutboundProxyPut").iter_errors(_put(password="s3cret")))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://gate.decodo.com:7000",
        "user:pw@gate.decodo.com:7000",
        "gate.decodo.com",
        "gate.decodo.com:",
        "a b:80",
    ],
)
def test_the_endpoint_is_host_and_port_only(endpoint: str) -> None:
    assert list(_validator("OutboundProxyPut").iter_errors(_put(endpoint=endpoint)))


def test_scheme_and_applies_to_are_bounded() -> None:
    v = _validator("OutboundProxyPut")
    assert list(v.iter_errors(_put(scheme="socks5")))
    assert list(v.iter_errors(_put(applies_to=[])))
    assert list(v.iter_errors(_put(applies_to=["agent_runs"])))
    assert list(v.iter_errors(_put(applies_to=["web_fetch", "web_fetch"])))
    assert not list(v.iter_errors(_put(applies_to=["web_fetch"])))
    assert SCHEMAS["OutboundProxyPut"]["properties"]["applies_to"]["default"] == KINDS
    assert SCHEMAS["OutboundProxy"]["properties"]["applies_to"]["items"]["enum"] == KINDS


def test_adr_0087_is_listed() -> None:
    adr = ROOT / "docs" / "adr" / "0087-an-organisation-has-one-outbound-proxy.md"
    assert "accepted, 2026-10-26" in adr.read_text()
    assert adr.name in (ROOT / "docs" / "adr" / "README.md").read_text()
