"""The same call under approval is the same action (#1379, ADR 0035).

An ALLOWED write was keyed by `call_id(run, tool, arguments)`: replayed, it redoes nothing. A write
under approval was not: the same call made twice — a model retrying, a script run again after a fix
(ADR 0122) — proposed two actions, and a person could approve the same change twice. A proposal is now
keyed the same way; a different call is still a different action.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
RUN = "run-same-proposal"
TOOL = "/api/v1/internal/runs/run-same-proposal/tools/pfsense__update_firewall_rule"


@pytest.fixture
def firewall(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.netops import FakePfSense

    fake = FakePfSense()
    monkeypatch.setattr(choregos_adapters, "FAKE_NETOPS", {"pfsense": fake})
    monkeypatch.setenv("CONNECTOR_SAME_PROPOSAL_PFSENSE_KEY", "-".join(("pfsense", "same", "proposal")))
    yield fake


async def _run_token(client: AsyncClient, project: dict[str, Any]) -> str:
    """The pfSense connector (its rule update needs a person), a netops agent and one of its runs."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    body = {"name": "pfsense", "type": "pfsense", "config": {"base_url": "https://fw.example.test"},
            "secret_refs": {"api_key": "env:CONNECTOR_SAME_PROPOSAL_PFSENSE_KEY"}}  # fmt: skip
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    spec = {"mcp_servers": [{"connector": "pfsense", "tools": ["update_firewall_rule"]}]}
    agent = await client.post(
        f"{ORG}/agents", json={"slug": "netops", "display_name": "Netops", "spec": spec}
    )
    assert agent.status_code == 201, agent.text
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/net#9", title="open 9443", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=RUN,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="x",
                transition_id="t",
                status="running",
                agent_slug="netops",
                agent_version=1,
            )
        )
    return mint_run_token(RUN, project_slug="billing-api", work_item_key="varga/net#9", ttl_minutes=30)


async def test_the_same_call_under_approval_proposes_one_action(
    client: AsyncClient, project: dict[str, Any], firewall: Any
) -> None:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    token = await _run_token(client, project)
    headers = {"Authorization": f"Bearer {token}"}
    change = {"tracker": "1700000102", "changes": {"destination_port": "9443"}}

    first = await client.post(TOOL, headers=headers, json=change)
    again = await client.post(TOOL, headers=headers, json=change)
    script = await client.post(TOOL, headers={**headers, "X-Choregos-Tool-Origin": "script"}, json=change)
    other = await client.post(TOOL, headers=headers, json={**change, "changes": {"destination_port": "9444"}})

    for answer in (first, again, script, other):
        assert answer.status_code == 200 and answer.json()["status_code"] == 202, answer.text
    ids = [answer.json()["result"]["action"] for answer in (first, again, script, other)]
    assert ids[0] == ids[1] == ids[2], "the same call, through either door, is the same action"
    assert ids[3] != ids[0], "a different call is a different action"
    assert first.json()["result"]["decision_path"] == again.json()["result"]["decision_path"]
    async with session_scope() as session:
        count = (
            await session.execute(select(func.count()).select_from(Action).where(Action.run_id == RUN))
        ).scalar_one()
    assert count == 2, "one pending action per distinct call: a person never approves the same change twice"
    assert firewall.writes == 0, "nothing reaches the firewall before a person approves"
