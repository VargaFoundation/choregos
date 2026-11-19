"""Round C1 of ADR 0181 on the API: the neutral names beside the former ones, for one window.

A response carries both names of a renamed field; a request accepts either spelling of a renamed role,
and a row is written with the former one until the writers switch (`Role.stored`, ADR 0098 §4); a row
holding either spelling gives the same permissions and rank; the role groups `contributors` and
`approvers` sign in as `developers` and `release-captains` do. The former names leave in the release
`tranibor_contracts.window` names.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from tranibor_contracts import Role

from .conftest import login


async def _role_row(email: str) -> str:
    from sqlalchemy import select
    from tranibor_api.db.models import Membership, User
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        rows = (await session.execute(select(Membership.role).where(Membership.user_id == user.id))).scalars()
        return next(iter(rows))


async def _member(email: str, role: Role) -> None:
    """A row of the organisation `northwind` under the spelling given, as 0.29.0's writers write it."""
    from sqlalchemy import select
    from tranibor_api.db.models import Membership, Organization, User
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == "northwind"))
        ).scalar_one()
        user = User(email=email, display_name=email.split("@", maxsplit=1)[0])
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role=str(role)))


def test_a_role_and_its_former_name_hold_the_same_permissions_and_rank() -> None:
    from tranibor_api.rbac import ROLE_PERMISSIONS, Permission, rank_of

    assert ROLE_PERMISSIONS[Role.CONTRIBUTOR] == ROLE_PERMISSIONS[Role.DEVELOPER]
    assert ROLE_PERMISSIONS[Role.APPROVER] == ROLE_PERMISSIONS[Role.RELEASE_CAPTAIN]
    assert Permission.TRAIN_APPROVE in ROLE_PERMISSIONS[Role.APPROVER]
    assert Permission.TRAIN_APPROVE not in ROLE_PERMISSIONS[Role.CONTRIBUTOR]
    assert set(ROLE_PERMISSIONS) == set(Role)
    ranks = [rank_of(role) for role in ("viewer", "contributor", "approver", "project_owner", "org_admin")]
    assert ranks == sorted(ranks) and len(set(ranks)) == 5
    assert rank_of("developer") == rank_of(Role.CONTRIBUTOR)
    assert rank_of("release_captain") == rank_of(Role.APPROVER)
    assert rank_of("captain") == -1 and rank_of(None) == -1


@pytest.mark.parametrize(("sent", "stored"), [("contributor", "developer"), ("approver", "release_captain")])
async def test_a_member_given_a_neutral_role_is_stored_under_the_former_spelling(
    client: AsyncClient, project: dict[str, Any], sent: str, stored: str
) -> None:
    email = f"{sent}@northwind.dev"
    added = await client.post("/api/v1/orgs/northwind/members", json={"email": email, "role": sent})
    assert added.status_code in (200, 201), added.text
    assert added.json()["role"] == stored
    assert await _role_row(email) == stored
    listed = (await client.get("/api/v1/orgs/northwind/members")).json()
    rows = listed["items"] if isinstance(listed, dict) else listed
    assert {m["role"] for m in rows if m.get("email") == email} == {stored}


async def test_a_row_under_the_neutral_spelling_holds_its_permissions(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row written by 0.29.0 or by the enterprise edition's SCIM: `approver` approves releases."""
    from tranibor_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@northwind.dev")
    reset_settings_cache()
    await _member("neutral@northwind.dev", Role.APPROVER)
    async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="http://test") as c:  # type: ignore[attr-defined]
        await login(c, "neutral@northwind.dev")
        me = (await c.get("/api/v1/me")).json()
        assert {m["role"] for m in me["memberships"]} == {"approver"}, "the dev sign-in left the row alone"
        frozen = await c.post(
            f"/api/v1/projects/{project['id']}/trains/prod/freeze", json={"reason": "round C1"}
        )
        assert frozen.status_code not in (403, 422), frozen.text
    reset_settings_cache()


def test_the_role_groups_sign_in_under_both_spellings() -> None:
    from tranibor_api.routers.auth import roles_from_groups

    assert roles_from_groups(["choregos:acme:contributors"], "") == {"acme": Role.CONTRIBUTOR}
    assert roles_from_groups(["choregos:acme:developers"], "") == {"acme": Role.CONTRIBUTOR}
    assert roles_from_groups(["choregos:acme:approvers"], "") == {"acme": Role.APPROVER}
    assert roles_from_groups(["choregos:acme:release-captains"], "") == {"acme": Role.APPROVER}
    both = ["choregos:acme:developers", "choregos:acme:approvers", "choregos:beta:viewers"]
    assert roles_from_groups(both, "") == {"acme": Role.APPROVER, "beta": Role.VIEWER}
    assert Role.APPROVER.stored is Role.RELEASE_CAPTAIN, "until 0.29.0 the row keeps the former spelling"


async def test_a_work_item_carries_both_names(client: AsyncClient, project: dict[str, Any]) -> None:
    from tranibor_api.db.models import WorkItem
    from tranibor_api.db.session import session_scope

    pid = project["id"]
    await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "internal", "config": {}})
    created = await client.post(f"/api/v1/projects/{pid}/work-items", json={"title": "T", "start": False})
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["workspace"] == item["repository"] == "billing-api"
    assert item["workspace_fixed"] is item["repository_fixed"] is False
    assert item["links"] == [] and item["pr_url"] is None
    async with session_scope(orgs="*") as session:
        row = await session.get(WorkItem, item["id"])
        assert row is not None
        row.pr_url = "https://github.com/northwind/billing-api/pull/7"
    again = (await client.get(f"/api/v1/work-items/{item['id']}")).json()
    assert again["pr_url"] == "https://github.com/northwind/billing-api/pull/7"
    assert again["links"] == [{"rel": "change_request", "url": again["pr_url"]}]


def test_the_dtos_fill_each_name_from_the_other() -> None:
    from tranibor_api.schemas import MemoryAbGroup
    from tranibor_api.schemas.projects import ProjectStats

    assert ProjectStats(first_pass_acceptance_rate=0.5).first_pass_merge_rate == 0.5
    assert ProjectStats(first_pass_merge_rate=0.25).first_pass_acceptance_rate == 0.25
    assert MemoryAbGroup(first_pass_merge_rate=0.75).first_pass_acceptance_rate == 0.75
    assert MemoryAbGroup(first_pass_acceptance_rate=0.1).first_pass_merge_rate == 0.1


async def test_the_catalogue_and_the_process_view_carry_both_names(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    entries = (await client.get("/api/v1/orgs/northwind/workflow-catalogue")).json()
    entries = entries["items"] if isinstance(entries, dict) else entries
    assert entries, "the catalogue lists the shipped workflows"
    assert all(e["needs_workspace"] == e["needs_repository"] for e in entries)
    assert any(e["needs_workspace"] for e in entries) and not all(e["needs_workspace"] for e in entries)


def test_the_core_views_carry_both_names() -> None:
    import yaml
    from tranibor_contracts import Workflow
    from tranibor_core.dsl import template_yaml
    from tranibor_core.dsl.flow import artifact_flow
    from tranibor_core.dsl.process import to_process

    workflow = Workflow.model_validate(yaml.safe_load(template_yaml("dev-feature")))
    steps = to_process(workflow)
    assert all(s["writes_workspace"] == s["writes_branch"] for s in steps)
    assert any(s["writes_workspace"] for s in steps)
    flows = artifact_flow(workflow)
    assert flows and all(a["in_change_request"] == a["in_pull_request"] for a in flows)


async def test_an_approval_rule_ranks_a_role_under_either_spelling() -> None:
    from types import SimpleNamespace

    from tranibor_api.services.actions import _rank_asked, rank

    assert _rank_asked({"role": "contributor"}) == _rank_asked({"role": "developer"})
    assert _rank_asked({"role": "approver"}) == _rank_asked({"role": "release_captain"})
    assert _rank_asked({"role": "nonsense"}) == _rank_asked({}) == _rank_asked({"role": "project_owner"})
    principal = SimpleNamespace(role_for=lambda org, project: Role.APPROVER, org_roles={"acme": Role.VIEWER})
    assert rank(principal, "acme", "p") == _rank_asked({"role": "release_captain"})
