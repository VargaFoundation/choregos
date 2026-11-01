"""The demo `cd` (S21-24): what the train of a demo project promotes, served by the fakes' process
so that the orchestrator and the API see the same environment."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.fakes.demo_cd import DemoCd, FakeDemoCd
from choregos_adapters.fakes.hr import Desk, RemoteDesk
from choregos_adapters.fakes.server import DemoApplication
from choregos_adapters.mcp import McpClient
from choregos_core.domain import Change

TOKEN = "demo-fakes-token"


def _served(app: DemoApplication, token: str = TOKEN) -> DemoCd:
    client = McpClient("http://demo/cd/mcp", token=token, transport=httpx.ASGITransport(app=app))
    return DemoCd(RemoteDesk(client))


async def test_the_train_promotes_and_the_api_reads_the_same_environment() -> None:
    app = DemoApplication(token=TOKEN)
    orchestrator, api = _served(app), _served(app)
    assert (await api.health("dev")).status == "Missing", "never promoted: not healthy"

    ref = await orchestrator.promote("prod", [Change(app="dev", tag="R-2026.10.07-1")], "R-2026.10.07-1")
    assert ref.merged and ref.url == "demo://prod/1"
    health = await api.health("dev")
    assert (health.status, health.revision) == ("Healthy", "R-2026.10.07-1")
    assert (await api.rollout_status("dev")).phase == "Healthy"
    assert await api.current_revision("dev") == "R-2026.10.07-1"

    # A replayed activity does not promote twice.
    await orchestrator.promote("prod", [Change(app="dev", tag="R-2026.10.07-1")], "R-2026.10.07-1")
    assert len(app.fakes["cd"].promotions) == 1  # type: ignore[attr-defined]


async def test_a_promotion_broken_on_purpose_shows_and_is_undone() -> None:
    """To show a rollback: the next promotion arrives degraded; the train cancels it."""
    fake = FakeDemoCd()
    cd = DemoCd(Desk(fake))
    await cd.desk.execute("break_next_rollout", {})
    await cd.promote("prod", [Change(app="dev", tag="R-1")], "R-1")
    assert (await cd.health("dev")).status == "Degraded"
    assert (await cd.rollout_status("dev")).phase == "Degraded"
    await cd.abort_rollout("dev")
    assert (await cd.rollout_status("dev")).phase == "Aborted"

    await cd.promote("prod", [Change(app="dev", tag="R-2")], "R-2")
    assert (await cd.health("dev")).status == "Healthy", "only the next promotion was broken"


async def test_a_read_writes_nothing() -> None:
    fake = FakeDemoCd()
    cd = DemoCd(Desk(fake))
    before = fake.state()
    await cd.health("dev")
    await cd.rollout_status("dev")
    await cd.current_revision("dev")
    assert fake.state() == before


def test_the_demo_type_is_refused_in_staging_and_prod_and_reaches_the_fakes_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_adapters import available, build

    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    assert "demo" in available("cd")
    for environment in ("staging", "prod"):
        monkeypatch.setenv("CHOREGOS_ENV", environment)
        # The message is the registry's (S45-10 translates it).
        with pytest.raises(ConfigurationError, match="refusé"):  # lang: fr-ok
            build("cd", "demo", {})
    monkeypatch.setenv("CHOREGOS_ENV", "dev")
    remote: Any = build("cd", "demo", {"url": "http://choregos-demo-fakes:8090/cd/mcp", "api_key": TOKEN})
    assert isinstance(remote, DemoCd) and isinstance(remote.desk, RemoteDesk)
    assert remote.desk.client.url == "http://choregos-demo-fakes:8090/cd/mcp"
    local: Any = build("cd", "demo", {})
    assert isinstance(local.desk, Desk), "without a url: in memory"
