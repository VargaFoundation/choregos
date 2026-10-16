"""Release channels through the API and the database (S40-02a, ADR 0071).

Production and testing are two pointers at immutable revisions; "publish" saves a revision, "release"
moves a pointer. Every case below names the guard it proves: the guard removed once turns it red.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .agent_scopes_kit import ADMIN, ORG, add_member, as_user, create_agent, install_common, make_project

pytestmark = pytest.mark.usefixtures("admin")

CANDIDATE_POLICY = {"on_publish": "candidate", "stable": {"require_candidate": True}}


def spec(n: int = 1, **extra: Any) -> dict[str, Any]:
    return {"instructions": f"Review the change, revision {n}.", **extra}


async def new_agent(
    client: AsyncClient, slug: str = "reviewer", policy: dict[str, Any] | None = None, **extra: Any
) -> Any:
    body: dict[str, Any] = {"spec": spec(1), **extra}
    if policy is not None:
        body["release_policy"] = policy
    response = await create_agent(client, slug, **body)
    assert response.status_code == 201, response.text
    return response.json()


async def save(client: AsyncClient, slug: str, n: int, **extra: Any) -> dict[str, Any]:
    response = await client.post(f"{ORG}/agents/{slug}/versions", json=spec(n, **extra))
    assert response.status_code == 201, response.text
    return dict(response.json())


async def agent_of(client: AsyncClient, slug: str = "reviewer") -> dict[str, Any]:
    response = await client.get(f"{ORG}/agents/{slug}")
    assert response.status_code == 200, response.text
    return dict(response.json())


async def release(client: AsyncClient, slug: str, **body: Any) -> Any:
    return await client.post(f"{ORG}/agents/{slug}/releases", json=body)


async def served_version(client: AsyncClient, project_id: str, ref: str = "reviewer") -> dict[str, Any]:
    response = await client.get(f"/api/v1/projects/{project_id}/agent-resolution", params={"ref": ref})
    assert response.status_code == 200, response.text
    return dict(response.json()[0])


async def finished_runs(
    project_id: str, slug: str, version: int, statuses: list[str], key: str = "R"
) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        for index, status in enumerate(statuses):
            item = WorkItem(
                project_id=project_id, tracker_key=f"{key}-{version}-{index}", title="T", state="ready"
            )
            session.add(item)
            await session.flush()
            session.add(
                Run(
                    id=f"{key}-{slug}-{version}-{index}",
                    work_item_id=item.id,
                    project_id=project_id,
                    stage_role="implement",
                    status=status,
                    agent_slug=slug,
                    agent_version=version,
                )
            )


async def token_client(app: Any, client: AsyncClient) -> AsyncClient:
    """A client that holds an API token of the signed-in administrator and no session."""
    token = (await client.post("/api/v1/me/tokens", json={"name": "ci", "scopes": ["*"]})).json()["token"]
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def only_project_roles() -> None:
    """A person who belongs to projects only: dropping the organisation role a first sign-in gives."""
    from choregos_api.db.models import Membership
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete, select

    async with session_scope() as session:
        await session.execute(
            delete(Membership).where(
                Membership.project_id.is_(None),
                Membership.user_id.in_(select(Membership.user_id).where(Membership.project_id.is_not(None))),
            )
        )


# 1 ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_the_default_policy_keeps_todays_behaviour(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Red if `after_publish` is not called: the resolver reads production, which would stay on v1."""
    await as_user(client, ADMIN)
    created = await new_agent(client)
    assert (created["stable_version"], created["candidate_version"]) == (1, None)
    assert created["release_policy"]["on_publish"] == "stable"
    await save(client, "reviewer", 2)
    agent = await agent_of(client)
    assert (agent["stable_version"], agent["candidate_version"], agent["latest_version"]) == (2, None, 2)
    answer = await served_version(client, project["id"])
    assert (answer["version"], answer["source"], answer["channel"]) == (2, "stable", "stable")
    assert [v["channels"] for v in agent["versions"]] == [["stable"], []]


# 2 ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_on_publish_candidate_serves_testing_only_to_those_who_follow_it(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await as_user(client, ADMIN)
    await new_agent(client, policy=CANDIDATE_POLICY)
    await save(client, "reviewer", 2)
    agent = await agent_of(client)
    assert (agent["stable_version"], agent["candidate_version"], agent["candidate_percent"]) == (1, 2, 0)
    pid = project["id"]
    assert (await served_version(client, pid))["version"] == 1, "a plain project stays on production"

    channel = f"/api/v1/projects/{pid}/agent-channel"
    assert (await client.get(channel)).json() == {"channel": "stable"}
    assert (await client.put(channel, json={"channel": "candidate"})).status_code == 200
    follows = await served_version(client, pid)
    assert (follows["version"], follows["source"], follows["channel"]) == (2, "project_channel", "candidate")
    assert (follows["candidate_version"], follows["candidate_percent"]) == (2, 0)
    assert (await client.put(channel, json={"channel": "stable"})).status_code == 200

    pin = f"/api/v1/projects/{pid}/agents/reviewer"
    on_channel = await client.put(pin, json={"channel": "candidate"})
    assert on_channel.status_code == 200 and on_channel.json()["version"] == 2
    assert on_channel.json()["channel"] == "candidate"
    answer = await served_version(client, pid)
    assert (answer["version"], answer["source"], answer["pinned"]) == (2, "pin_channel", True)
    fixed = await client.put(pin, json={"version": 1})
    assert fixed.status_code == 200 and fixed.json()["channel"] is None
    assert (await client.put(channel, json={"channel": "candidate"})).status_code == 200
    answer = await served_version(client, pid)
    assert (answer["version"], answer["source"]) == (1, "pin"), "a fixed pin beats the project's switch"
    both = await client.put(pin, json={"version": 1, "channel": "stable"})
    neither = await client.put(pin, json={})
    assert both.status_code == 422 and neither.status_code == 422, "exactly one of version and channel"

    assert (await client.delete(pin)).status_code == 204
    withdrawn = await release(client, "reviewer", channel="candidate", version=None)
    assert withdrawn.status_code == 201 and withdrawn.json()["kind"] == "withdraw"
    agent = await agent_of(client)
    assert (agent["candidate_version"], agent["candidate_percent"]) == (None, 0)
    assert (await served_version(client, pid))["version"] == 1, "after the withdrawal everyone is back on 1"


# 3 ──────────────────────────────────────────────────────────────────────────────────────────────


async def resolve_for(project_id: str, work_item: str) -> Any:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.agents import resoudre_l_agent

    async with session_scope() as session:
        projet = await session.get(Project, project_id)
        assert projet is not None
        return await resoudre_l_agent(session, projet.org_id, projet.id, "reviewer", work_item_id=work_item)


async def test_a_canary_share_is_deterministic_per_work_item(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await as_user(client, ADMIN)
    await new_agent(client, policy=CANDIDATE_POLICY)
    await save(client, "reviewer", 2)
    sent = await release(client, "reviewer", channel="candidate", version=2, percent=100)
    assert sent.status_code == 201, sent.text
    assert sent.json()["percent"] == 100
    pid = project["id"]
    for item in ("w-1", "w-2", "w-3"):
        resolved = await resolve_for(pid, item)
        assert (resolved.version, resolved.source, resolved.channel) == (2, "canary", "candidate")
    assert (await resolve_for(pid, "w-1")).version == (await resolve_for(pid, "w-1")).version
    assert (await served_version(client, pid))["version"] == 1, "the endpoint has no work item: no canary"
    assert (await served_version(client, pid))["candidate_percent"] == 100

    assert (await release(client, "reviewer", channel="candidate", version=2, percent=0)).status_code == 201
    assert [(await resolve_for(pid, f"w-{n}")).version for n in range(10)] == [1] * 10
    half = await release(client, "reviewer", channel="candidate", version=2, percent=50)
    assert half.status_code == 201
    first = [(await resolve_for(pid, f"w-{n}")).version for n in range(40)]
    again = [(await resolve_for(pid, f"w-{n}")).version for n in range(40)]
    assert first == again and {1, 2} == set(first), "a 50 % share splits the items, the same way each time"


# 4 ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_production_checks_the_suite_and_the_candidates_record(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await as_user(client, ADMIN)
    policy = {
        "on_publish": "candidate",
        "stable": {"require_candidate": True, "min_candidate_runs": 2, "min_success_rate": 0.5},
    }
    await new_agent(client, policy=policy)
    await save(client, "reviewer", 2)
    blocked = await release(client, "reviewer", channel="stable")
    assert blocked.status_code == 409, blocked.text
    by_name = {e["loc"][1]: e for e in blocked.json()["errors"]}
    assert by_name["candidate_runs"]["type"] == "release_check_failed"
    assert (await agent_of(client))["stable_version"] == 1

    readiness = (
        await client.get(f"{ORG}/agents/reviewer/versions/2/readiness", params={"channel": "stable"})
    ).json()
    checks = {c["name"]: c for c in readiness["checks"]}
    assert readiness["ready"] is False and checks["candidate_runs"]["passed"] is False
    assert checks["eval_suite"]["passed"] is True and checks["is_candidate"]["passed"] is True
    assert checks["fresh_authentication"]["passed"] is True

    await finished_runs(project["id"], "reviewer", 2, ["succeeded", "failed"])
    done = await release(client, "reviewer", channel="stable", reason="two runs, one failure")
    assert done.status_code == 201, done.text
    body = done.json()
    assert (body["kind"], body["version"], body["previous_version"], body["status"]) == (
        "promote",
        2,
        1,
        "done",
    )
    assert any(c["name"] == "candidate_runs" and c["passed"] for c in body["checks"]), "the snapshot is kept"
    agent = await agent_of(client)
    assert (agent["stable_version"], agent["candidate_version"]) == (2, None), "production took the candidate"


async def test_production_checks_the_eval_suite_again_at_release(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A revision that gets into the database without passing the level-1 gate does not reach
    production: red without the re-check at release."""
    from choregos_api.db.models import Agent, AgentVersion
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await as_user(client, ADMIN)
    suite = [{"name": "mentions review", "must_contain": ["Review"]}]
    await new_agent(
        client, "guarded", policy={"on_publish": "none"}, spec={"instructions": "Review it.", "evals": suite}
    )
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "guarded"))).scalar_one()
        session.add(
            AgentVersion(
                agent_id=agent.id,
                org_id=agent.org_id,
                version=2,
                spec={"instructions": "Say nothing useful."},
                checksum="sha256:bad",
            )
        )
    refused = await release(client, "guarded", channel="stable", version=2)
    assert refused.status_code == 409, refused.text
    assert refused.json()["errors"][0]["loc"] == ["checks", "eval_suite"]
    assert (await agent_of(client, "guarded"))["stable_version"] == 1
    sent = await release(client, "guarded", channel="candidate", version=2)
    assert sent.status_code == 409, "nor does it reach testing"


async def test_a_revision_must_be_newer_than_production_to_be_released(client: AsyncClient) -> None:
    await as_user(client, ADMIN)
    await new_agent(client)
    await save(client, "reviewer", 2)
    again = await release(client, "reviewer", channel="stable", version=2)
    assert again.status_code == 409 and "already in production" in again.text
    nothing = await release(client, "reviewer", channel="stable")
    assert nothing.status_code == 409 and "testing" in nothing.text
    missing = await release(client, "reviewer", channel="stable", version=9)
    assert missing.status_code == 404
    stale = await release(client, "reviewer", channel="candidate", version=1)
    assert stale.status_code == 409 and stale.json()["errors"][0]["loc"] == [
        "checks",
        "newer_than_production",
    ]


# 5 ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_a_rollback_skips_the_gates_but_only_to_a_revision_that_was_in_production(
    client: AsyncClient,
) -> None:
    await as_user(client, ADMIN)
    await new_agent(client)
    await save(client, "reviewer", 2)
    await save(client, "reviewer", 3)
    assert (await agent_of(client))["stable_version"] == 3
    strict = {"on_publish": "candidate", "stable": {"require_candidate": True, "min_candidate_runs": 2}}
    assert (await client.put(f"{ORG}/agents/reviewer/release-policy", json=strict)).status_code == 200
    back = await release(client, "reviewer", channel="stable", version=2, reason="v3 misbehaves")
    assert back.status_code == 201, back.text
    assert back.json()["kind"] == "rollback" and back.json()["previous_version"] == 3
    assert [c["name"] for c in back.json()["checks"] if c["required"]][:1] == ["previously_in_production"]
    assert (await agent_of(client))["stable_version"] == 2

    await save(client, "reviewer", 4)  # goes to testing under the new policy
    skip = await release(client, "reviewer", channel="stable", version=4)
    assert skip.status_code == 409, "a promotion keeps every gate"
    assert {e["loc"][1] for e in skip.json()["errors"]} == {"candidate_runs"}


async def test_an_older_revision_that_was_never_in_production_is_not_a_rollback(client: AsyncClient) -> None:
    await as_user(client, ADMIN)
    await new_agent(client, policy={"on_publish": "none"})
    await save(client, "reviewer", 2)
    await save(client, "reviewer", 3)
    assert (await release(client, "reviewer", channel="stable", version=3)).json()["kind"] == "promote"
    older = await release(client, "reviewer", channel="stable", version=2)
    assert older.status_code == 409, "v2 was never in production: a promotion, with its gates"
    assert older.json()["errors"][0]["loc"] == ["checks", "newer_than_production"]
    assert (await release(client, "reviewer", channel="stable", version=1)).json()["kind"] == "rollback"


async def test_a_migrated_agent_rolls_back_to_any_version_up_to_its_baseline(client: AsyncClient) -> None:
    from choregos_api.db.models import Agent, AgentRelease, AgentVersion
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await as_user(client, ADMIN)
    await new_agent(client, "legacy")
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "legacy"))).scalar_one()
        for version in (2, 3):
            session.add(
                AgentVersion(
                    agent_id=agent.id,
                    org_id=agent.org_id,
                    version=version,
                    spec=spec(version),
                    checksum=f"sha256:{version}",
                )
            )
        agent.stable_version = 3
        session.add(
            AgentRelease(
                org_id=agent.org_id,
                agent_id=agent.id,
                channel="stable",
                kind="baseline",
                version=3,
                status="done",
                requested_by="migration",
                checks=[],
            )
        )
    first = await release(client, "legacy", channel="stable", version=1)
    assert first.status_code == 201 and first.json()["kind"] == "rollback", first.text


# 6 ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_production_needs_a_person_with_a_fresh_sign_in(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import fraicheur

    await as_user(client, ADMIN)
    await new_agent(client, policy={"on_publish": "candidate"})
    await save(client, "reviewer", 2)

    async with await token_client(app, client) as machine:
        refused = await release(machine, "reviewer", channel="stable", version=2)
        assert refused.status_code == 403 and "human session" in refused.text
        to_testing = await release(machine, "reviewer", channel="candidate", version=2, percent=0)
        assert to_testing.status_code == 201, "a token may send to testing without a share"
        canary = await release(machine, "reviewer", channel="candidate", version=2, percent=10)
        assert canary.status_code == 403, "a canary share is a person's decision"
        withdrawn = await release(machine, "reviewer", channel="candidate", version=None)
        assert withdrawn.status_code == 201 and withdrawn.json()["kind"] == "withdraw"

    later = time.time() + 11 * 60
    monkeypatch.setattr(fraicheur.time, "time", lambda: later)
    old = await release(client, "reviewer", channel="stable", version=2)
    assert old.status_code == 401 and old.json()["errors"][0]["error"] == "step_up_required"
    assert (await agent_of(client))["stable_version"] == 1, "production is unchanged"
    old_canary = await release(client, "reviewer", channel="candidate", version=2, percent=10)
    assert old_canary.status_code == 401
    readiness = (await client.get(f"{ORG}/agents/reviewer/versions/2/readiness")).json()
    fresh = next(c for c in readiness["checks"] if c["name"] == "fresh_authentication")
    assert fresh["passed"] is False and "10 min" in fresh["detail"]
    monkeypatch.undo()
    assert (await release(client, "reviewer", channel="stable", version=2)).status_code == 201


async def test_a_personal_agent_is_released_without_a_step_up(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import fraicheur

    await add_member(client, "dev@varga.dev", "developer")
    await as_user(client, "dev@varga.dev")
    await new_agent(client, "mine", policy={"on_publish": "candidate"}, scope="personal")
    await save(client, "mine", 2)
    later = time.time() + 60 * 60
    monkeypatch.setattr(fraicheur.time, "time", lambda: later)
    done = await release(client, "mine", channel="stable", version=2)
    assert done.status_code == 201, done.text


# 7 ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_who_may_release(client: AsyncClient) -> None:
    await make_project(client, "dev")
    await add_member(client, "owner@varga.dev", "project_owner", project="dev")
    await add_member(client, "dev@varga.dev", "developer")
    await as_user(client, ADMIN)
    await new_agent(client, "shared")
    await save(client, "shared", 2)
    await as_user(client, "dev@varga.dev")
    await new_agent(client, "private", scope="personal")
    await as_user(client, "owner@varga.dev")
    shared = await release(client, "shared", channel="candidate", version=2)
    assert shared.status_code == 403 and "organisation agent" in shared.text
    assert (await agent_of(client, "shared"))["can"]["release"] is False
    own = await create_agent(client, "dev-helper", scope="project", project="dev")
    assert own.status_code == 201, own.text
    await save(client, "dev-helper", 2)
    assert (await agent_of(client, "dev-helper"))["can"]["release"] is True
    assert (await release(client, "dev-helper", channel="stable", version=2)).status_code == 409
    assert (await release(client, "dev-helper", channel="stable", version=1)).status_code == 201
    hidden = await release(client, "private", channel="candidate", version=1)
    assert hidden.status_code == 404, "someone else's personal agent does not exist for the caller"


# 8 ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_the_plugin_gesture_check_can_refuse_a_release(client: AsyncClient) -> None:
    from choregos_api import greffons

    def refuse(_session: Any, demand: Any) -> None:
        assert demand.cible["channel"] == "stable" and demand.cible["kind"] == "promote"
        raise greffons.GesteRefuse("the organisation says no")

    await as_user(client, ADMIN)
    await new_agent(client, policy={"on_publish": "candidate"})
    await save(client, "reviewer", 2)
    greffons.declarer_un_controle_de_geste("agent.release", "test", refuse)
    try:
        refused = await release(client, "reviewer", channel="stable", version=2)
        assert refused.status_code == 403 and "test: the organisation says no" in refused.text
        assert (await agent_of(client))["stable_version"] == 1, "nothing moved"
        withdraw = await release(client, "reviewer", channel="candidate", version=None)
        assert withdraw.status_code == 201, "a withdrawal is not a gated gesture"
    finally:
        greffons.reinitialiser_les_controles()
    assert (await release(client, "reviewer", channel="candidate", version=2)).status_code == 201


# 9 ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_every_move_is_in_the_history_and_in_the_audit_log(client: AsyncClient) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await as_user(client, ADMIN)
    await new_agent(client, policy={"on_publish": "none"})
    await save(client, "reviewer", 2)
    await release(client, "reviewer", channel="candidate", version=2, percent=20, reason="try it")
    await release(client, "reviewer", channel="stable", version=2)
    await release(client, "reviewer", channel="stable", version=1, reason="back")
    history = (await client.get(f"{ORG}/agents/reviewer/releases")).json()
    assert [(r["channel"], r["kind"], r["version"]) for r in history][:3] == [
        ("stable", "rollback", 1),
        ("stable", "promote", 2),
        ("candidate", "promote", 2),
    ]
    assert history[-1]["kind"] == "publish" and history[-1]["version"] == 1, "creation is the first move"
    assert history[0]["reason"] == "back" and history[0]["requested_by"] == ADMIN
    assert history[1]["checks"] and history[1]["checks"][0]["name"] == "eval_suite"
    assert len((await client.get(f"{ORG}/agents/reviewer/releases", params={"limit": 2})).json()) == 2
    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == "agent.release"))).scalars()
        payloads = [r.payload for r in rows if r.payload.get("kind") != "publish"]
    assert [(p["channel"], p["version"], p["previous_version"], p["kind"]) for p in payloads] == [
        ("candidate", 2, None, "promote"),
        ("stable", 2, 1, "promote"),
        ("stable", 1, 2, "rollback"),
    ]


# 10 ─────────────────────────────────────────────────────────────────────────────────────────────


async def test_the_exposure_lists_who_chose_their_own_revision(client: AsyncClient) -> None:
    pins = await make_project(client, "pinned")
    chan = await make_project(client, "on-channel")
    follow = await make_project(client, "following")
    await make_project(client, "plain")
    await add_member(client, "po@varga.dev", "project_owner", project="pinned")
    await as_user(client, ADMIN)
    await new_agent(client, policy=CANDIDATE_POLICY)
    await save(client, "reviewer", 2)
    assert (
        await client.put(f"/api/v1/projects/{pins['id']}/agents/reviewer", json={"version": 1})
    ).status_code == 200
    assert (
        await client.put(f"/api/v1/projects/{chan['id']}/agents/reviewer", json={"channel": "candidate"})
    ).status_code == 200
    assert (
        await client.put(f"/api/v1/projects/{follow['id']}/agent-channel", json={"channel": "candidate"})
    ).status_code == 200
    exposure = (await client.get(f"{ORG}/agents/reviewer/exposure")).json()
    assert (exposure["stable_version"], exposure["candidate_version"]) == (1, 2)
    assert {(p["project"], p["source"], p["version"]) for p in exposure["projects"]} == {
        ("pinned", "pin", 1),
        ("on-channel", "pin_channel", 2),
        ("following", "project_channel", 2),
    }
    await as_user(client, "po@varga.dev")
    await only_project_roles()
    visible = (await client.get(f"{ORG}/agents/reviewer/exposure")).json()["projects"]
    assert [p["project"] for p in visible] == ["pinned"], "a project the caller may not read is not listed"


# 11 ─────────────────────────────────────────────────────────────────────────────────────────────


async def test_the_release_policy_is_the_owners_and_loosening_it_needs_a_fresh_sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import fraicheur

    await make_project(client, "dev")
    await add_member(client, "owner@varga.dev", "project_owner", project="dev")
    await as_user(client, ADMIN)
    await new_agent(client, policy=CANDIDATE_POLICY)
    await as_user(client, "owner@varga.dev")
    refused = await client.put(f"{ORG}/agents/reviewer/release-policy", json={"on_publish": "none"})
    assert refused.status_code == 403
    await as_user(client, ADMIN)
    later = time.time() + 11 * 60
    monkeypatch.setattr(fraicheur.time, "time", lambda: later)
    loosening = await client.put(f"{ORG}/agents/reviewer/release-policy", json={"on_publish": "stable"})
    assert loosening.status_code == 401 and loosening.json()["errors"][0]["error"] == "step_up_required"
    assert (await agent_of(client))["release_policy"]["on_publish"] == "candidate"
    tightening = await client.put(
        f"{ORG}/agents/reviewer/release-policy",
        json={"on_publish": "candidate", "stable": {"require_candidate": True, "min_candidate_runs": 3}},
    )
    assert tightening.status_code == 200, "tightening needs no fresh sign-in"
    invalid = await client.put(
        f"{ORG}/agents/reviewer/release-policy",
        json={"on_publish": "stable", "stable": {"min_soak_hours": 2}},
    )
    assert invalid.status_code == 422
    monkeypatch.undo()
    ok = await client.put(f"{ORG}/agents/reviewer/release-policy", json={"on_publish": "stable"})
    assert ok.status_code == 200 and ok.json()["release_policy"]["on_publish"] == "stable"


# 12 ─────────────────────────────────────────────────────────────────────────────────────────────


async def test_installed_forked_and_upgraded_agents_have_a_production_pointer(client: AsyncClient) -> None:
    from choregos_api.db.models import AgentVersion
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await as_user(client, ADMIN)
    common = await install_common(client)
    assert common["stable_version"] == 1 and common["can"]["release"] is True
    fork = await client.post(f"{ORG}/agents/developer/fork", json={"slug": "my-dev", "scope": "org"})
    assert fork.status_code == 201 and fork.json()["stable_version"] == 1
    assert fork.json()["release_policy"]["on_publish"] == "stable", "a fork inherits no policy"
    assert (
        await client.put(f"{ORG}/agents/developer/release-policy", json={"on_publish": "none"})
    ).status_code == 200
    async with session_scope() as session:  # make the catalogue see a change
        row = (await session.execute(select(AgentVersion).where(AgentVersion.version == 1))).scalars().first()
        assert row is not None
        row.checksum = "sha256:old"
    upgrade = await client.post(f"{ORG}/agent-catalogue/developer/install", json={"upgrade": True})
    assert upgrade.status_code in {200, 201}, upgrade.text
    agent = await agent_of(client, "developer")
    assert agent["latest_version"] >= 2 and agent["stable_version"] == 1, "on_publish none: production stays"
    assert agent["candidate_version"] is None


# retirement ─────────────────────────────────────────────────────────────────────────────────────


async def test_retiring_an_agent_empties_testing(client: AsyncClient) -> None:
    await as_user(client, ADMIN)
    await new_agent(client, policy=CANDIDATE_POLICY)
    await save(client, "reviewer", 2)
    assert (await agent_of(client))["candidate_version"] == 2
    retired = await client.post(f"{ORG}/agents/reviewer/lifecycle", json={"to": "retired"})
    assert retired.status_code == 200, retired.text
    assert retired.json()["agent"]["candidate_version"] is None
    assert (await release(client, "reviewer", channel="candidate", version=2)).status_code == 409


async def test_a_release_made_meanwhile_is_a_conflict_not_a_lost_update(client: AsyncClient) -> None:
    """The pointer moves with a compare-and-set from the revision the checks saw: a gesture check
    that lets another release slip in (a stand-in for a concurrent request) makes this one a 409."""
    from choregos_api import greffons
    from choregos_api.db.models import Agent
    from sqlalchemy import update

    await as_user(client, ADMIN)
    await new_agent(client, policy={"on_publish": "none"})
    await save(client, "reviewer", 2)
    await save(client, "reviewer", 3)

    async def concurrent(session: Any, _demand: Any) -> None:
        await session.execute(update(Agent).where(Agent.slug == "reviewer").values(stable_version=3))

    greffons.declarer_un_controle_de_geste("agent.release", "race", concurrent)
    try:
        refused = await release(client, "reviewer", channel="stable", version=2)
    finally:
        greffons.reinitialiser_les_controles()
    assert refused.status_code == 409 and "another release" in refused.text
    # (the stand-in ran inside the refused request, so it is rolled back with it)
    assert (await agent_of(client))["stable_version"] == 1
    assert len((await client.get(f"{ORG}/agents/reviewer/releases")).json()) == 1, "no row for a refused move"
