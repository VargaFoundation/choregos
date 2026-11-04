# SPDX-License-Identifier: Apache-2.0
"""The optional fake MCP gateway of the local install (S46-15, ADR 0105): the shape of compose.yaml."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "compose.yaml"
OPTIONAL = ("demo-fakes", "mcp-gateway")


def _services() -> dict[str, Any]:
    return dict(yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"])


def _env(service: dict[str, Any]) -> dict[str, str]:
    return {k: str(v) for k, v in (service.get("environment") or {}).items()}


def test_the_gateway_and_the_fakes_are_optional() -> None:
    services = _services()
    for name in OPTIONAL:
        assert name in services, name
        assert services[name].get("profiles") == ["mcp-gateway"], name


def test_the_gateway_fronts_the_demo_fakes_by_name() -> None:
    upstreams = json.loads(_env(_services()["mcp-gateway"])["CHOREGOS_FAKE_GATEWAY_UPSTREAMS"])
    assert upstreams
    for name, upstream in upstreams.items():
        assert name in {"mdm", "shipping", "access_control", "supplier", "cd"}
        assert upstream["url"].startswith("http://demo-fakes:8090/"), name
        assert upstream["url"].endswith("/mcp"), name


def test_the_api_accepts_the_gateway_host() -> None:
    for name in ("api", "worker"):
        hosts = json.loads(_env(_services()[name])["CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS"])
        assert hosts == ["mcp-gateway"], name


def test_the_gateway_publishes_no_port() -> None:
    for name in OPTIONAL:
        assert "ports" not in _services()[name], name
