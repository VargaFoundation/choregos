"""The fakes of the HR scenario served by a single process (S20-07): what a deployment with several
processes — the dev API and orchestrator — must see the same.

An in-memory fake keeps its state in its process: the orchestrator enrolled a device that the API,
reading the fleet back, did not see. Served by `DemoApplication`, two clients — two processes —
see the same fleet, the same directory.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import httpx
import pytest
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes.hr import RemoteDesk
from choregos_adapters.fakes.server import DemoApplication, application
from choregos_adapters.mcp import McpClient, McpError

TOKEN = "demo-fakes-token"


def _desk(app: DemoApplication, family: str, token: str = TOKEN) -> RemoteDesk:
    return RemoteDesk(
        McpClient(f"http://demo/{family}/mcp", token=token, transport=httpx.ASGITransport(app=app))
    )


async def test_two_processes_see_the_same_fleet() -> None:
    app = DemoApplication(token=TOKEN)
    orchestrator, api = _desk(app, "mdm"), _desk(app, "mdm")
    enrolled = await orchestrator.execute("enroll_device", {"serial": "PC-1", "upn": "lea@acme.test"})
    assert enrolled["enrolled"] is True
    assert (await api.execute("device_status", {"serial": "PC-1"}))["state"] == "enrolled"
    with pytest.raises(AdapterError, match="inscrit pour un autre utilisateur"):  # lang: fr-ok (pinned)
        await api.execute("enroll_device", {"serial": "PC-1", "upn": "paul@acme.test"})


async def test_the_fake_graph_is_reached_by_its_address_and_its_secret() -> None:
    from choregos_adapters.identity import EntraIdentity

    app = DemoApplication(token=TOKEN)

    def directory(secret: str) -> EntraIdentity:
        return EntraIdentity(
            tenant_id="acme",
            client_id="choregos",
            client_secret=secret,
            administrative_unit_id=app.directory.unit,
            graph_url="http://demo/graph/v1.0",
            login_url="http://demo/login",
            transport=httpx.ASGITransport(app=app),
        )

    created = await directory(TOKEN).create_user("lea@acme.test", "Léa Martin")
    read_back = await directory(TOKEN).get_user("lea@acme.test")
    assert read_back is not None and read_back["id"] == created["id"], "another client reads the account"
    with pytest.raises(AdapterError, match="token refused"):
        await directory("another-secret").get_user("lea@acme.test")


async def test_a_wrong_token_is_refused_and_the_application_holds() -> None:
    app = DemoApplication(token=TOKEN)
    with pytest.raises(McpError) as refusal:
        await _desk(app, "access_control", token="wrong").execute("badge_status", {"uid": "04A1"})
    assert refusal.value.code == 401
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo") as http:
        assert (await http.get("/healthz")).json() == {"ok": True}
        assert (await http.post("/elsewhere/mcp", json={})).status_code == 404


async def _gates(app: DemoApplication) -> tuple[bool, bool]:
    """Whether the MCP endpoint refuses a wrong token, and accepts the right one."""
    with pytest.raises(McpError) as refusal:
        await _desk(app, "mdm", token="wrong").execute("device_status", {"serial": "PC-1"})
    answered = await _desk(app, "mdm", token=TOKEN).execute("device_status", {"serial": "PC-1"})
    return refusal.value.code == 401, answered["state"] == "unknown"


@pytest.mark.parametrize("variable", ["CONNECTOR_DEMO_TOKEN", "CONNECTOR_DEMO_JETON"])  # lang: fr-ok
async def test_demo_token_reads_english_and_former_variable(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """Each variable gates the MCP endpoints; the English one wins when both are set."""
    monkeypatch.delenv("CONNECTOR_DEMO_TOKEN", raising=False)
    monkeypatch.delenv("CONNECTOR_DEMO_JETON", raising=False)  # lang: fr-ok
    monkeypatch.setenv(variable, TOKEN)
    expected = (
        pytest.warns(DeprecationWarning, match="removed in 0.22.0")
        if variable.endswith("JETON")
        else nullcontext()
    )
    with expected:
        app = application()
    assert await _gates(app) == (True, True)
    monkeypatch.setenv("CONNECTOR_DEMO_TOKEN", TOKEN)
    monkeypatch.setenv("CONNECTOR_DEMO_JETON", "ignored-former-value")  # lang: fr-ok
    assert await _gates(application()) == (True, True), "the English variable wins"
    monkeypatch.delenv("CONNECTOR_DEMO_TOKEN")
    monkeypatch.delenv("CONNECTOR_DEMO_JETON")  # lang: fr-ok
    assert (await _desk(application(), "mdm", token="").execute("device_status", {"serial": "PC-1"}))[
        "state"
    ] == "unknown", "neither variable set: no token asked"


def test_a_demo_connector_that_carries_a_url_reaches_the_fakes_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_adapters import build
    from choregos_adapters.fakes.hr import Desk

    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    monkeypatch.setenv("CHOREGOS_ENV", "dev")
    remote: Any = build("mdm", "demo", {"url": "http://choregos-demo-fakes:8090/mdm/mcp", "api_key": TOKEN})
    assert isinstance(remote, RemoteDesk)
    assert remote.client.url == "http://choregos-demo-fakes:8090/mdm/mcp"
    assert isinstance(build("mdm", "demo", {}), Desk), "without a url: in memory"
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    assert isinstance(build("mdm", "demo", {"url": "http://elsewhere/mcp"}), Desk), (
        "under CHOREGOS_FAKES, nothing leaves the process"
    )
