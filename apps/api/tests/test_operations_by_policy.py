"""The organisation's connectors, and the policy of each operation (ADR 0034, S19-02).

The organisation's administrator declares an instance and decides, operation by operation,
whether it is allowed, subject to approval or forbidden, and for which groups of projects. A
project only narrows.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login

CREATE_ACCOUNT = "/api/v1/orgs/varga/connectors/directory-acme/operations/create_account"


@pytest.fixture
def directory() -> Iterator[None]:
    """A directory type, declared as an extension would: its operations make its policy."""
    from choregos_adapters import ConnectorTypeSpec, OperationSpec, register
    from choregos_adapters.registry import _REGISTRY, _SPECS

    spec = ConnectorTypeSpec(
        "test directory",
        ("identity",),
        {"type": "object", "properties": {"tenant": {"type": "string"}}},
        ("client_secret",),
        operations=(
            OperationSpec("read_user", "read"),
            OperationSpec("create_account", "write", "creates an account"),
            OperationSpec("deactivate_account", "write"),
        ),
    )
    register("identity", "directory", spec)(lambda cfg: object())
    yield
    _REGISTRY.pop(("identity", "directory"), None)
    _SPECS.pop(("identity", "directory"), None)


async def _declare(client: AsyncClient, **extra: Any) -> Any:
    body = {"name": "directory-acme", "type": "directory", "config": {"tenant": "acme"},
             "secret_refs": {"client_secret": "env:CONNECTOR_DIRECTORY_SECRET"}, **extra}  # fmt: skip
    return await client.post("/api/v1/orgs/varga/connectors", json=body)


def _policies(operations: list[dict[str, Any]]) -> dict[str, str]:
    return {o["operation"]: o["effective_policy"] for o in operations}


async def test_an_instance_is_born_with_its_operations_at_their_default_policy(
    client: AsyncClient, admin: str, directory: None
) -> None:
    created = await _declare(client)
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "identity"
    assert {o["name"]: o["policy"] for o in created.json()["operations"]} == {
        "create_account": "approval",
        "deactivate_account": "approval",
        "read_user": "allowed",
    }
    assert (await _declare(client)).status_code == 409
    in_clear = await client.post(
        "/api/v1/orgs/varga/connectors",
        json={"name": "another", "type": "directory", "config": {"client_secret": "s3cr3t"}},
    )
    assert in_clear.status_code == 422 and "s3cr3t" not in in_clear.text


async def test_a_project_narrows_but_never_turns_approval_into_allowed(
    client: AsyncClient, project: dict[str, Any], directory: None
) -> None:
    pid = project["id"]
    assert (await _declare(client)).status_code == 201
    base = f"/api/v1/projects/{pid}/operations/directory-acme"
    assert _policies((await client.get(f"/api/v1/projects/{pid}/operations")).json()) == {
        "create_account": "approval",
        "deactivate_account": "approval",
        "read_user": "allowed",
    }
    widened = await client.put(f"{base}/create_account", json={"policy": "allowed"})
    assert widened.status_code == 422 and "only narrow" in widened.text

    narrowed = await client.put(f"{base}/read_user", json={"policy": "approval"})
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["effective_policy"] == "approval"
    assert (await client.put(f"{base}/deactivate_account", json={"policy": "forbidden"})).status_code == 200
    seen = _policies((await client.get(f"/api/v1/projects/{pid}/operations")).json())
    assert seen == {
        "create_account": "approval",
        "deactivate_account": "forbidden",
        "read_user": "approval",
    }

    # The organisation then hardens: the project follows, its own line does not exempt it.
    hardened = await client.patch("/api/v1/orgs/varga/connectors/directory-acme/operations/read_user",
                                json={"policy": "forbidden"})  # fmt: skip
    assert hardened.status_code == 200
    assert (
        _policies((await client.get(f"/api/v1/projects/{pid}/operations")).json())["read_user"] == "forbidden"
    )

    assert (await client.delete(f"{base}/deactivate_account")).status_code == 204
    assert (
        _policies((await client.get(f"/api/v1/projects/{pid}/operations")).json())["deactivate_account"]
        == "approval"
    )


async def test_the_groups_of_an_operation_say_which_projects_see_it(
    client: AsyncClient, project: dict[str, Any], directory: None
) -> None:
    pid = project["id"]
    assert (await _declare(client)).status_code == 201
    groups_response = await client.patch(CREATE_ACCOUNT, json={"groups": ["hr"]})
    assert groups_response.status_code == 200 and groups_response.json()["groups"] == ["hr"]
    assert "create_account" not in _policies((await client.get(f"/api/v1/projects/{pid}/operations")).json())
    config = {**project["config"], "groups": ["hr"]}
    assert (await client.patch(f"/api/v1/projects/{pid}", json={"config": config})).status_code == 200
    assert "create_account" in _policies((await client.get(f"/api/v1/projects/{pid}/operations")).json())


async def test_a_project_owner_decides_neither_connectors_nor_groups(
    client: AsyncClient, project: dict[str, Any], directory: None
) -> None:
    assert (await _declare(client)).status_code == 201
    member = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "po@varga.dev", "role": "project_owner"}
    )
    assert member.status_code in {200, 201}, member.text
    await login(client, "po@varga.dev")
    groups_response = await client.patch(CREATE_ACCOUNT, json={"groups": ["finance"]})
    assert groups_response.status_code == 403
    assert (await client.patch(CREATE_ACCOUNT, json={"policy": "allowed"})).status_code == 403
    assert (await _declare(client, name="pirate")).status_code == 403
    # He reads, and he narrows his project.
    assert (await client.get("/api/v1/orgs/varga/connectors")).status_code == 200
    narrowed = await client.put(
        f"/api/v1/projects/{project['id']}/operations/directory-acme/read_user",
        json={"policy": "approval"},
    )
    assert narrowed.status_code == 200, narrowed.text


async def test_another_organisation_does_not_exist(client: AsyncClient, admin: str, directory: None) -> None:
    from choregos_api.db.models import Organization, OrgConnector
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        other = Organization(slug="other", name="Other")
        session.add(other)
        await session.flush()
        session.add(
            OrgConnector(
                org_id=other.id, name="secret-of-other", kind="identity", type="directory", config={}
            )
        )
    # Two organisations: varga's administrator no longer writes an `env:` reference (ADR 0088).
    assert (await _declare(client, secret_refs={})).status_code == 201
    names = [c["name"] for c in (await client.get("/api/v1/orgs/varga/connectors")).json()]
    assert names == ["directory-acme"]
    assert (await client.get("/api/v1/orgs/other/connectors")).status_code == 403
