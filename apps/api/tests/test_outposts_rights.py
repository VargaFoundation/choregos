"""Who may manage outposts, and what an outpost itself may do: nothing (S65-11, ADR 0147 §3-4).

`outpost:manage` is the organisation administrator's and no other role's. An outpost's instance is a
principal kind of its own: no membership, no role, hence no permission; it holds no session, no API
token and no OAuth token, and the REST API, the MCP door and the A2A door refuse whatever presents its
signature. The door that reads that signature is not here (S65-12).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
from typing import Any

import choregos_contracts as contracts
import pytest
from choregos_contracts import SIGNATURE_HEADER, Role
from httpx import ASGITransport, AsyncClient

from .conftest import login
from .kits import outposts as kit
from .kits.outposts import BASE

SLUG = "factory-floor"
ONE = f"{BASE}/{SLUG}"
FINGERPRINT = {"fingerprint": "SHA256:" + "a" * 43}
NOT_ADMINISTRATORS = [Role.VIEWER, Role.DEVELOPER, Role.RELEASE_CAPTAIN, Role.PROJECT_OWNER]


def _routes(instance: str) -> list[tuple[str, str, str, Any]]:
    """(operation, method, path, a VALID body): an invalid one would be a 422 before the right is read."""
    return [
        ("listOutposts", "GET", BASE, None),
        ("createOutpost", "POST", BASE, {"slug": "cold-store"}),
        ("getOutpost", "GET", ONE, None),
        ("pauseOutpost", "POST", f"{ONE}/pause", None),
        ("resumeOutpost", "POST", f"{ONE}/resume", None),
        ("createOutpostEnrolment", "POST", f"{ONE}/enrolments", None),
        ("listOutpostInstances", "GET", f"{ONE}/instances", None),
        ("approveOutpostInstance", "POST", f"{ONE}/instances/{instance}/approve", FINGERPRINT),
        ("revokeOutpostInstance", "POST", f"{ONE}/instances/{instance}/revoke", None),
        ("listOutpostCalls", "GET", f"{ONE}/calls", None),
        ("revokeOutpost", "POST", f"{ONE}/revoke", None),
        ("deleteOutpost", "DELETE", ONE, None),
    ]


async def _member(email: str, role: Role) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        org = (await session.execute(select(Organization).where(Organization.slug == kit.ORG))).scalar_one()
        user = User(email=email, display_name=email.split("@", maxsplit=1)[0])
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role=str(role)))


def test_outpost_manage_is_the_organisation_administrators_and_no_other_roles() -> None:
    from choregos_api.rbac import ROLE_PERMISSIONS, Permission

    assert Permission.OUTPOST_MANAGE.value == "outpost:manage"
    holders = {
        role for role, permissions in ROLE_PERMISSIONS.items() if Permission.OUTPOST_MANAGE in permissions
    }
    assert holders == {Role.ORG_ADMIN}


def test_the_routes_under_test_are_the_contracts_twelve() -> None:
    declared = {
        operation["operationId"]
        for path, verbs in contracts.load_openapi()["paths"].items()
        if path.startswith("/orgs/{org}/outposts")
        for operation in verbs.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert len(declared) == 12 and declared == {name for name, *_ in _routes("0" * 64)}


@pytest.mark.parametrize("role", NOT_ADMINISTRATORS, ids=[str(role) for role in NOT_ADMINISTRATORS])
async def test_without_outpost_manage_every_route_answers_403(
    client: AsyncClient, app: Any, admin: str, monkeypatch: pytest.MonkeyPatch, role: Role
) -> None:
    kit.switch_on(monkeypatch)
    await kit.create(client)
    instance = await kit.enrolled_instance(client)
    email = f"{role}@varga.dev"
    await _member(email, role)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as member:
        await login(member, email)
        me = (await member.get("/api/v1/me")).json()
        assert {m["role"] for m in me["memberships"]} == {str(role)}, "the dev sign-in touched the role"
        answers = {
            name: (await member.request(method, path, json=body)).status_code
            for name, method, path, body in _routes(instance)
        }
    assert answers == dict.fromkeys(answers, 403), answers
    outpost = (await client.get(ONE)).json()
    assert (outpost["status"], outpost["epoch"], outpost["instances"]["pending"]) == ("active", 0, 1)
    assert [o["slug"] for o in (await client.get(BASE)).json()["items"]] == [SLUG], "and nothing moved"


async def test_the_administrator_is_refused_by_none_of_them(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit.switch_on(monkeypatch)
    await kit.create(client)
    instance = await kit.enrolled_instance(client)
    answers = {
        name: (await client.request(method, path, json=body)).status_code
        for name, method, path, body in _routes(instance)
    }
    assert 403 not in answers.values() and 401 not in answers.values(), answers
    assert answers["approveOutpostInstance"] == 409, "the fingerprint sent is not the instance's"


async def test_without_a_session_the_routes_answer_401(client: AsyncClient, org: str) -> None:
    for _, method, path, body in _routes("0" * 64):
        assert (await client.request(method, path, json=body)).status_code == 401, f"{method} {path}"


# ───────────────────────── the principal kind `outpost` ─────────────────────────


def test_an_outposts_instance_is_a_principal_without_membership_or_role() -> None:
    from choregos_api.rbac import OUTPOST, Permission, outpost_principal

    principal = outpost_principal(outpost_id="outpost-1", instance_id="instance-1")
    assert principal.kind == OUTPOST == "outpost"
    assert (principal.org_roles, principal.project_roles, principal.groups) == ({}, {}, [])
    assert principal.authenticated_at is None and principal.session_id is None, "it holds no session"
    assert principal.user_id == "outpost:outpost-1:instance-1" and principal.email == ""
    assert not any(principal.can(permission, "varga") for permission in Permission)
    assert not principal.is_platform_admin()


def test_an_outpost_holds_no_permission_whatever_roles_it_is_handed() -> None:
    """The kind is the guard, not the absence of a role: a later change that gave such a principal a
    role would still give it nothing."""
    from choregos_api.rbac import Permission, outpost_principal

    principal = outpost_principal(outpost_id="outpost-1", instance_id="instance-1")
    principal.org_roles = {"varga": Role.ORG_ADMIN}
    principal.project_roles = {"varga/billing-api": Role.PROJECT_OWNER}
    assert principal.permissions("varga") == frozenset()
    assert principal.permissions("varga", "billing-api") == frozenset()
    assert not any(principal.can(permission, "varga", "billing-api") for permission in Permission)
    assert not principal.is_platform_admin()


def _signature() -> str:
    """A real signature of an instance: the contract's own vector."""
    path = contracts.schemas_dir() / "examples" / "outpost-signature.vector.json"
    vector = json.loads(path.read_text(encoding="utf-8"))
    return str(next(r["signature"] for r in vector["requests"] if r["name"] == "poll"))


async def test_the_rest_api_refuses_a_request_that_carries_an_outposts_signature(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request is never an outpost's and a person's at once: the signature is refused before the
    session is read, on a route of the outposts' management as on any other."""
    kit.switch_on(monkeypatch)
    signed = {SIGNATURE_HEADER: _signature()}
    for path in ("/api/v1/me", BASE):
        assert (await client.get(path)).status_code == 200, "the administrator's own session opens it"
        refused = await client.get(path, headers=signed)
        assert refused.status_code == 403 and "outposts' door only" in refused.json()["detail"], refused.text
    created = await client.post(BASE, json={"slug": SLUG}, headers=signed)
    assert created.status_code == 403 and (await client.get(BASE)).json() == {"items": []}

    client.cookies.clear()
    assert (await client.get(BASE, headers=signed)).status_code == 403, "nor does the signature alone"
    as_a_token = await client.get(BASE, headers={"Authorization": f"Bearer {_signature()}"})
    assert as_a_token.status_code == 401, "and it is not an API token"


async def test_the_mcp_door_refuses_a_request_that_carries_an_outposts_signature(
    client: AsyncClient, admin: str
) -> None:
    """An outpost lists no tool of the platform and calls none (ADR 0148 §1)."""
    token = (await client.post("/api/v1/me/tokens", json={"name": "c", "scopes": ["mcp:read"]})).json()[
        "token"
    ]
    client.cookies.clear()
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    bearer = {"Authorization": f"Bearer {token}"}
    assert (await client.post("/mcp", json=ping, headers=bearer)).status_code == 200
    refused = await client.post("/mcp", json=ping, headers=bearer | {SIGNATURE_HEADER: _signature()})
    assert refused.status_code == 403 and "outposts' door only" in refused.json()["error"], refused.text
    alone = await client.post("/mcp", json=ping, headers={SIGNATURE_HEADER: _signature()})
    assert alone.status_code == 403
    as_a_token = await client.post("/mcp", json=ping, headers={"Authorization": f"Bearer {_signature()}"})
    assert as_a_token.status_code == 401


async def test_the_a2a_door_refuses_a_request_that_carries_an_outposts_signature(
    client: AsyncClient, admin: str
) -> None:
    """An outpost executes what the platform decided and starts nothing (ADR 0147 §3)."""
    token = (await client.post("/api/v1/me/tokens", json={"name": "p", "scopes": ["a2a"]})).json()["token"]
    client.cookies.clear()
    card = "/a2a/orgs/varga/.well-known/agent-card.json"
    bearer = {"Authorization": f"Bearer {token}"}
    assert (await client.get(card, headers=bearer)).status_code == 200
    refused = await client.get(card, headers=bearer | {SIGNATURE_HEADER: _signature()})
    assert refused.status_code == 403 and "outposts' door only" in refused.json()["error"], refused.text
    message = {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {}}
    sent = await client.post(
        "/a2a/orgs/varga", json=message, headers=bearer | {SIGNATURE_HEADER: _signature()}
    )
    assert sent.status_code == 403
    as_a_token = await client.get(card, headers={"Authorization": f"Bearer {_signature()}"})
    assert as_a_token.status_code == 401


def test_the_service_a_door_will_import_reaches_no_organisation_connector() -> None:
    """The outposts' door meets a call only as a row (ADR 0148 §12): its directory joins the doors the
    boundary test freezes, whose whole import closure is refused the broker, the gateway services and
    the MCP client. The door imports this service to enrol an instance, so the service is held to the
    same list now, read from the boundary test itself."""
    root = pathlib.Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "mcp_gateway_boundary_for_outposts", root / "tests" / "licences" / "test_mcp_gateway_boundary.py"
    )
    assert spec is not None and spec.loader is not None
    boundary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boundary)
    forbidden = boundary.FORBIDDEN_MODULES | boundary.PLUGIN_SERVER_SURFACE
    probe = "import json, sys; import choregos_api.services.outposts; print(json.dumps(sorted(sys.modules)))"
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=False, cwd=root
    )
    assert done.returncode == 0, done.stderr[-2000:]
    closure = json.loads(done.stdout.strip().splitlines()[-1])
    assert "choregos_api.services.outposts" in closure
    reached = [module for module in closure if boundary._is_forbidden(module, forbidden)]
    assert not reached, f"importing the outposts service imports {reached}: a door could not import it"
