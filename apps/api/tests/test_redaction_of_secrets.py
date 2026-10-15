"""A resolved connector secret does not leave the process in an error or a log line (S39-06c, ADR 0058).

The pattern rules of `choregos_core.redaction` know token SHAPES; a vendor key has none. What the
platform resolved is remembered, and masked wherever it would be written."""

from __future__ import annotations

import base64
import io
import json
import os
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from httpx import AsyncClient

VALUE = "plain-vendor-key-9f8e7d"


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from choregos_core.redaction import forget_secrets

    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", "k1:" + base64.b64encode(os.urandom(32)).decode())
    forget_secrets()
    yield
    forget_secrets()


@pytest.fixture
def leaky(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A tracker type whose factory fails with the key in its message, as a careless SDK would."""
    from choregos_adapters import ConnectorTypeSpec, register
    from choregos_adapters.registry import _REGISTRY, _SPECS

    def factory(config: dict[str, Any]) -> Any:
        raise RuntimeError(f"upstream refused the key {config['api_token']} for this account")

    spec = ConnectorTypeSpec("leaky", ("tracker",), {"type": "object", "properties": {}}, ("api_token",))
    register("tracker", "leaky", spec)(factory)
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    try:
        yield
    finally:
        _REGISTRY.pop(("tracker", "leaky"), None)
        _SPECS.pop(("tracker", "leaky"), None)


async def test_a_failing_connector_test_leaves_no_secret_in_its_answer_or_last_error(
    client: AsyncClient, project: dict[str, Any], leaky: None
) -> None:
    pid = project["id"]
    put = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker",
        json={"type": "leaky", "config": {}, "secret_values": {"api_token": VALUE}},
    )
    assert put.status_code == 200, put.text
    result = await client.post(f"/api/v1/projects/{pid}/connectors/tracker/test")
    assert result.json()["ok"] is False
    assert "upstream refused the key" in result.text, "the message stays useful"
    assert VALUE not in result.text and "[REDACTED]" in result.text
    stored = (await client.get(f"/api/v1/projects/{pid}/connectors")).json()[0]
    assert stored["status"] == "error" and VALUE not in json.dumps(stored)
    assert "[REDACTED]" in stored["last_error"]


def test_the_log_processor_masks_remembered_values_in_every_field() -> None:
    from choregos_api.logging import redact_known_secrets
    from choregos_core.redaction import remember_secret

    remember_secret(VALUE)
    event = redact_known_secrets(
        None,
        "warning",
        {"event": f"call failed for {VALUE}", "extra": {"url": f"https://x/?k={VALUE}"}, "n": 3},
    )
    assert VALUE not in json.dumps(event) and event["n"] == 3


def test_a_configured_logger_does_not_print_a_remembered_value(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.logging import configure_logging, get_logger
    from choregos_core.redaction import remember_secret

    out = io.StringIO()
    configure_logging("INFO", True)
    structlog.configure(
        processors=structlog.get_config()["processors"],
        logger_factory=structlog.PrintLoggerFactory(file=out),
        cache_logger_on_first_use=False,
    )
    remember_secret(VALUE)
    get_logger("t").warning("connector.failed", error=f"HTTP 401 for key {VALUE}")
    assert out.getvalue() and VALUE not in out.getvalue() and "[REDACTED]" in out.getvalue()
