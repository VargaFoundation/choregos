"""A decision or a confirmation needs the person's session (S67-06, ADR 0154 §7, G10).

An API token with every scope (a machine account's, an MCP or A2A client's) proposes; it does not decide.
The console cookie, a frame token and a developer surface's session (S67-05) decide. Two routes accepted a
token before: a deployment may keep them for one minor version (`TRANIBOR_LEGACY_DECISION_ROUTES`).
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_api.config import Settings, get_settings
from tranibor_api.decision_session import CODE, holds_a_person_session, require_person_session
from tranibor_api.errors import ApiError
from tranibor_api.rbac import Principal

from .agent_scopes_kit import ADMIN, as_user
from .test_agent_release_channels import token_client
from .test_human_task import _waiting_work_item


def _principal(credential: str, kind: str = "user") -> Principal:
    return Principal(user_id="u1", email="a@b.test", kind=kind, credential=credential)  # type: ignore[arg-type]


@pytest.mark.parametrize("credential", ["cookie", "frame", "surface"])
def test_a_session_of_the_person_decides(credential: str) -> None:
    assert holds_a_person_session(_principal(credential))
    require_person_session(_principal(credential), "deciding")


@pytest.mark.parametrize("credential", ["api_token", ""])
def test_a_token_or_an_unknown_credential_does_not(credential: str) -> None:
    with pytest.raises(ApiError) as refused:
        require_person_session(_principal(credential), "deciding")
    assert refused.value.extra == {"code": CODE} and refused.value.status_code == 403


def test_a_machine_account_does_not_even_with_a_session_credential() -> None:
    assert not holds_a_person_session(_principal("cookie", kind="machine"))


def test_the_legacy_setting_reopens_only_the_route_it_names() -> None:
    settings = Settings(legacy_decision_routes="post_decision")
    require_person_session(_principal("api_token"), "deciding", settings=settings, legacy="post_decision")
    with pytest.raises(ApiError):
        require_person_session(_principal("api_token"), "x", settings=settings, legacy="conversation_actions")
    with pytest.raises(ApiError):
        require_person_session(_principal("api_token"), "x", settings=settings)


def test_the_setting_names_only_routes_that_can_be_reopened() -> None:
    with pytest.raises(ValueError, match="approve_release"):
        Settings(legacy_decision_routes="post_decision,approve_release")


async def test_a_token_does_not_decide_a_human_step(
    client: AsyncClient, app: Any, project: dict[str, Any]
) -> None:
    ticket = await _waiting_work_item(client, project["id"], kind="approval")
    url = f"/api/v1/work-items/{ticket['id']}/decisions"
    async with await token_client(app, client) as machine:
        refused = await machine.post(url, json={"kind": "approve"})
    assert refused.status_code == 403 and refused.json()["code"] == CODE
    assert (await client.post(url, json={"kind": "approve"})).status_code != 403


async def test_a_deployment_can_keep_post_decision_open_for_one_minor_version(
    client: AsyncClient, app: Any, project: dict[str, Any]
) -> None:
    ticket = await _waiting_work_item(client, project["id"], kind="approval")
    url = f"/api/v1/work-items/{ticket['id']}/decisions"
    app.dependency_overrides[get_settings] = lambda: Settings(legacy_decision_routes="post_decision")
    try:
        async with await token_client(app, client) as machine:
            allowed = await machine.post(url, json={"kind": "approve"})
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert allowed.status_code != 403, allowed.text


async def test_a_token_does_not_confirm_or_refuse_an_assistants_write(client: AsyncClient, app: Any) -> None:
    await as_user(client, ADMIN)
    async with await token_client(app, client) as machine:
        for verb in ("confirm", "refuse"):
            refused = await machine.post(f"/api/v1/me/conversations/c1/actions/a1/{verb}")
            assert refused.status_code == 403 and refused.json()["code"] == CODE, verb
        app.dependency_overrides[get_settings] = lambda: Settings(
            legacy_decision_routes="conversation_actions"
        )
        try:
            kept = await machine.post("/api/v1/me/conversations/c1/actions/a1/confirm")
        finally:
            app.dependency_overrides.pop(get_settings, None)
    assert kept.status_code == 404, "the legacy setting lets the token through to the lookup"


async def test_a_token_does_not_approve_a_release_nor_decide_an_action(
    client: AsyncClient, app: Any, project: dict[str, Any]
) -> None:
    async with await token_client(app, client) as machine:
        release = await machine.post("/api/v1/releases/r1/approve", json={"note": ""})
        action = await machine.post(
            f"/api/v1/projects/{project['id']}/actions/a1/decision", json={"decision": "approve"}
        )
        settled = await machine.post(
            f"/api/v1/projects/{project['id']}/actions/a1/settlement",
            json={"outcome": "made", "note": "found it"},
        )
    for refused in (release, action, settled):
        assert refused.status_code == 403 and refused.json()["code"] == CODE, refused.text


@pytest.mark.parametrize("credential", ["api_token", ""])
async def test_a_fresh_signed_in_principal_is_refused_when_its_credential_is_not_a_session(
    credential: str,
) -> None:
    """`authenticated_at` is set (a token minted from a fresh sign-in), the credential is what refuses."""
    from tranibor_api.schemas.actions import ActionSettlementBody
    from tranibor_api.services import actions
    from tranibor_core import utcnow

    principal = Principal(
        user_id="u1", email="a@b.test", kind="user", credential=credential, authenticated_at=utcnow()
    )  # type: ignore[arg-type]
    with pytest.raises(ApiError) as decision:
        await actions.decide(None, None, "northwind", "a1", "approve", None, principal)  # type: ignore[arg-type]
    with pytest.raises(ApiError) as settlement:
        await actions.settle(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            "northwind",
            "a1",
            ActionSettlementBody(outcome="made", note="found it"),
            principal,
        )
    assert decision.value.extra == settlement.value.extra == {"code": CODE}


async def test_a_token_does_not_decide_an_agent_release(client: AsyncClient, app: Any) -> None:
    await as_user(client, ADMIN)
    async with await token_client(app, client) as machine:
        refused = await machine.post(
            "/api/v1/orgs/northwind/agents/none/releases/r1/decision", json={"decision": "approve"}
        )
    assert refused.status_code == 403 and refused.json()["code"] == CODE
