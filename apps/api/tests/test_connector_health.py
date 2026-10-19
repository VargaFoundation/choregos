"""S40-05: a connector is tested when it is saved, and a failing test does not refuse the save.

The status used to be computed by the *Test* button alone, and every save reset it to
`unknown`: a freshly configured connector read "Unknown" until somebody pressed a button.
"""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_api.config import get_settings
from httpx import AsyncClient

FAKE_TOKEN = "".join(["tok", "en-", "for-the-test"])  # built at runtime: no literal that looks like a key


class _Probe:
    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour

    async def test(self) -> dict[str, Any]:
        if self.behaviour == "raise":
            raise RuntimeError("401 from the tracker: bad credentials")
        if self.behaviour == "slow":
            await asyncio.sleep(5)
        return {"ok": True}


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Three `tracker` types whose `test()` succeeds, raises, or never answers (real adapters, no fakes)."""
    from choregos_adapters import ConnectorTypeSpec, register
    from choregos_adapters.registry import _REGISTRY, _SPECS

    names = {"scripted-ok": "ok", "scripted-raise": "raise", "scripted-slow": "slow"}
    for name, behaviour in names.items():
        spec = ConnectorTypeSpec(name, ("tracker",), {"type": "object", "properties": {}}, ("api_token",))
        register("tracker", name, spec)(lambda cfg, b=behaviour: _Probe(b))
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    try:
        yield
    finally:
        for name in names:
            _REGISTRY.pop(("tracker", name), None)
            _SPECS.pop(("tracker", name), None)


async def _put(client: AsyncClient, project: dict[str, Any], type_name: str) -> dict[str, Any]:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": type_name, "config": {}}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_saving_a_connector_tests_it(client: AsyncClient, project: dict[str, Any]) -> None:
    saved = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "github-issues", "config": {}}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "ok" and saved.json()["last_check_at"], "tested by the save itself"


async def test_a_failing_test_does_not_refuse_the_save(
    client: AsyncClient, project: dict[str, Any], scripted: None
) -> None:
    saved = await _put(client, project, "scripted-raise")
    assert saved["status"] == "error" and "bad credentials" in saved["last_error"]
    listed = (await client.get(f"/api/v1/projects/{project['id']}/connectors")).json()
    assert listed[0]["type"] == "scripted-raise", "the configuration is stored"


async def test_a_slow_test_is_cut_at_the_timeout(
    client: AsyncClient, project: dict[str, Any], scripted: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "connector_test_timeout_s", 0.1)
    saved = await _put(client, project, "scripted-slow")
    assert saved["status"] == "error" and "did not answer" in saved["last_error"]


async def test_setting_or_clearing_a_secret_retests(
    client: AsyncClient, project: dict[str, Any], scripted: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = project["id"]
    await _put(client, project, "scripted-ok")
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", f"k1:{key}")  # the deployment's sealing key (ADR 0058)
    set_ = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker/secrets/api_token", json={"value": FAKE_TOKEN}
    )
    assert set_.status_code == 200, set_.text
    assert set_.json()["status"] == "ok" and set_.json()["last_check_at"]
    cleared = await client.delete(f"/api/v1/projects/{pid}/connectors/tracker/secrets/api_token")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["status"] == "ok" and cleared.json()["last_check_at"]
