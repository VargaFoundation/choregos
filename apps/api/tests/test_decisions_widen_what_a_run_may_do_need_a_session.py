"""The routes that widen what a run or the platform may do need the person's session (#1926, ADR 0154 §7).

Egress requests, plugin installs, skill versions and assistant knowledge updates were open to a pasted API
token: a machine account could open a host to a run, install a plugin or let a skill run. Each is now refused
(403, `decision_requires_session`) without a console cookie, a frame token or a surface session; a deployment
may keep each open for one minor version (`TRANIBOR_LEGACY_DECISION_ROUTES`). Device requests and pending
memory stay as they were (the product owner's decision, ADR 0154 amendment of 2026-11-19).
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_api.config import Settings, get_settings
from tranibor_api.decision_session import CODE
from tranibor_api.rbac import Principal

from .agent_scopes_kit import ADMIN, ORG, as_user
from .test_agent_release_channels import token_client

#: (name in the legacy setting, url, body)
ROUTES = {
    "egress approve": ("egress_requests", f"{ORG}/egress/requests/r1/approve", {}),
    "egress refuse": ("egress_requests", f"{ORG}/egress/requests/r1/refuse", {}),
    "plugin install": ("plugin_installs", f"{ORG}/plugin-approvals/a1/approve", None),
    "skill version": ("skill_versions", f"{ORG}/skills/none/versions/1/approve", {}),
    "knowledge update": (
        "assistant_knowledge_updates",
        f"{ORG}/assistants/none/knowledge/updates/u1",
        {"decision": "accept"},
    ),
}


@pytest.mark.parametrize("route", ROUTES)
async def test_a_pasted_token_is_refused(client: AsyncClient, app: Any, route: str) -> None:
    _, url, body = ROUTES[route]
    await as_user(client, ADMIN)
    async with await token_client(app, client) as machine:
        refused = await machine.post(url, json=body)
    assert refused.status_code == 403 and refused.json()["code"] == CODE, refused.text


@pytest.mark.parametrize("route", ROUTES)
async def test_a_console_session_reaches_the_route(client: AsyncClient, route: str) -> None:
    _, url, body = ROUTES[route]
    await as_user(client, ADMIN)
    reached = await client.post(url, json=body)
    assert reached.status_code != 403 or reached.json().get("code") != CODE, reached.text


@pytest.mark.parametrize("route", ROUTES)
async def test_the_legacy_setting_reopens_only_its_route(client: AsyncClient, app: Any, route: str) -> None:
    name, url, body = ROUTES[route]
    await as_user(client, ADMIN)
    app.dependency_overrides[get_settings] = lambda: Settings(legacy_decision_routes=name)
    try:
        async with await token_client(app, client) as machine:
            kept = await machine.post(url, json=body)
            others = [
                await machine.post(other_url, json=other_body)
                for other, (other_name, other_url, other_body) in ROUTES.items()
                if other_name != name
            ]
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert kept.status_code != 403 or kept.json().get("code") != CODE, kept.text
    assert all(r.status_code == 403 and r.json()["code"] == CODE for r in others)


@pytest.mark.parametrize("kind", ["machine", "run", "outpost"])
def test_no_other_kind_of_principal_holds_a_person_session(kind: str) -> None:
    from tranibor_api.decision_session import holds_a_person_session

    principal = Principal(user_id="u1", email="a@b.test", kind=kind, credential="cookie")  # type: ignore[arg-type]
    assert not holds_a_person_session(principal)


def test_the_setting_still_refuses_a_route_that_may_not_be_reopened() -> None:
    with pytest.raises(ValueError, match="device_requests"):
        Settings(legacy_decision_routes="egress_requests,device_requests")
