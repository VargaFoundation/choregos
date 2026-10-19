"""Network access policies (ADR 0070): the ceiling, the project, the agent, the run's effective network."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient

from .conftest import login

AGENTS = "/api/v1/orgs/varga/agents"
CEILING_URL = "/api/v1/orgs/varga/policy-ceiling"

# The digest of this spec, computed on the base branch BEFORE `AgentSpec.network` existed.
LEGACY_SPEC = {"instructions": "You are an architect.", "model": "m", "memory": "agent"}
LEGACY_DIGEST = "sha256:bd87085c3edebdab4ec5d28ad8172c549620412290f935e037b372883faf21a1"
EMPTY_DIGEST = "sha256:fbcf4a2774de60c2342568a78f4e648b3f066c87015ea982bd0999608c0d1321"


def _policy(allow: list[str], **extra: Any) -> str:
    network = ", ".join([f"allow_domains: {allow!r}", *(f"{k}: {v}" for k, v in extra.items())]).replace(
        "'", '"'
    )
    return f"""apiVersion: choregos/v1
kind: Policy
metadata: {{ name: net, version: 1 }}
sandbox:
  network: {{ {network} }}
"""


async def _ceiling(client: AsyncClient, network: dict[str, Any]) -> Any:
    return await client.put(CEILING_URL, json={"ceiling": {"network": network}})


async def _run(project: dict[str, Any], run_id: str, **columns: Any) -> dict[str, str]:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key=f"BILLING-{run_id}", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="implement",
                status="running",
                **columns,
            )
        )
    token = mint_run_token(
        run_id, project_slug="billing-api", work_item_key=f"BILLING-{run_id}", ttl_minutes=30
    )
    return {"Authorization": f"Bearer {token}"}


# ───────────────────────── the ceiling and the project policy ─────────────────────────


async def test_a_bad_pattern_in_the_ceiling_is_refused_naming_it(client: AsyncClient, admin: str) -> None:
    refused = await _ceiling(client, {"deny_domains": ["https://x.example"], "allow_domains": ["10.0.0.1"]})
    assert refused.status_code == 422, refused.text
    messages = " ".join(e["msg"] for e in refused.json()["errors"])
    assert "https://x.example" in messages and "10.0.0.1" in messages
    assert (
        await _ceiling(client, {"allow_domains": ["GitHub.com"], "per_host_per_minute": 60})
    ).status_code == 200
    stored = (await client.get(CEILING_URL)).json()["ceiling"]
    assert stored["network"]["allow_domains"] == ["github.com"], "patterns are stored normalised"


async def test_a_project_policy_wider_than_the_ceilings_network_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    network = {
        "allow_domains": ["github.com", "pypi.org"],
        "deny_domains": ["gist.github.com"],
        "per_host_per_minute": 60,
    }
    assert (await _ceiling(client, network)).status_code == 200
    url = f"/api/v1/projects/{project['id']}/policy"
    wider = await client.put(url, json={"yaml": _policy(["github.com", "api.stripe.com"])})
    assert wider.status_code == 422, wider.text
    detail = wider.json()["detail"]
    assert "network.allow_domains: `api.stripe.com` is outside the organisation's allowed hosts" in detail
    assert "network.per_host_per_minute: unlimited exceeds the organisation's ceiling of 60" in detail
    assert {tuple(e["loc"]) for e in wider.json()["errors"]} >= {("policy", "network", "allow_domains")}

    within = await client.put(url, json={"yaml": _policy(["github.com"], per_host_per_minute=30)})
    assert within.status_code == 200, within.text
    assert within.json()["clamps"] == []


async def test_the_project_network_shows_the_clamps_the_deny_list_and_the_implied_repository_host(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    saved = await client.put(
        f"/api/v1/projects/{project['id']}/policy", json={"yaml": _policy(["github.com", "api.stripe.com"])}
    )
    assert saved.status_code == 200
    await _ceiling(client, {"allow_domains": ["github.com"], "deny_domains": ["tracker.example"]})
    read = await client.get(f"/api/v1/projects/{project['id']}/network")
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["policy"]["allow_domains"] == ["github.com", "api.stripe.com"], "as stored"
    assert body["ceiling"]["allow_domains"] == ["github.com"]
    assert body["effective"]["allow"] == ["github.com"]
    assert body["effective"]["deny"] == ["tracker.example"]
    assert body["effective"]["implied"] == ["github.com"], (
        "the host of https://github.com/varga/billing-api.git"
    )
    assert body["clamps"] == [
        "network.allow_domains: `api.stripe.com` is outside the organisation's allowed hosts"
    ]


# ───────────────────────── the agent ─────────────────────────


async def test_an_agent_cannot_name_a_host_outside_the_ceiling_at_create_publish_and_trial(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _ceiling(client, {"allow_domains": ["github.com", "pypi.org"], "deny_domains": ["gist.github.com"]})
    outside = {"instructions": "x", "network": {"domains": ["pypi.org", "api.stripe.com"]}}
    created = await client.post(AGENTS, json={"slug": "net-a", "display_name": "A", "spec": outside})
    assert created.status_code == 422, created.text
    assert "`api.stripe.com` is outside the organisation's allowed hosts" in created.text
    assert "`pypi.org`" not in created.text.replace("`pypi.org` is", "")

    inside = {"instructions": "x", "network": {"domains": ["pypi.org", "api.github.com"]}}
    ok = await client.post(AGENTS, json={"slug": "net-a", "display_name": "A", "spec": inside})
    assert ok.status_code == 201, ok.text
    assert ok.json()["versions"][0]["spec"]["network"]["domains"] == ["pypi.org", "api.github.com"]

    denied = {"instructions": "x", "network": {"domains": ["gist.github.com"]}}
    published = await client.post(f"{AGENTS}/net-a/versions", json=denied)
    assert published.status_code == 422, published.text
    assert "denied by the organisation" in published.text

    trial = await client.post(
        "/api/v1/orgs/varga/agent-trials",
        json={"project": "billing-api", "spec": outside, "input": {"title": "t", "body": "b"}},
    )
    assert trial.status_code == 422, trial.text
    assert "api.stripe.com" in trial.text


async def test_an_agent_network_with_a_bad_pattern_is_refused(client: AsyncClient, admin: str) -> None:
    spec = {
        "instructions": "x",
        "network": {"domains": ["https://x.example", "10.0.0.1"], "per_host_per_minute": 0},
    }
    refused = await client.post(AGENTS, json={"slug": "net-b", "display_name": "B", "spec": spec})
    assert refused.status_code == 422, refused.text


async def test_without_a_ceiling_any_pattern_is_publishable_and_empty_means_no_internet(
    client: AsyncClient, admin: str
) -> None:
    spec = {"instructions": "x", "network": {"domains": [], "builtin_web_tools": False}}
    ok = await client.post(AGENTS, json={"slug": "offline", "display_name": "Offline", "spec": spec})
    assert ok.status_code == 201, ok.text
    assert ok.json()["versions"][0]["spec"]["network"] == {
        "domains": [],
        "per_host_per_minute": None,
        "builtin_web_tools": False,
    }


def test_the_digest_of_a_version_without_a_network_has_not_moved() -> None:
    """Versions published before `network` existed keep the digest they were published with."""
    from choregos_api.schemas.agents import AgentNetwork, AgentSpec
    from choregos_api.services.agents import empreinte

    assert empreinte(AgentSpec.model_validate(LEGACY_SPEC)) == LEGACY_DIGEST
    assert empreinte(AgentSpec()) == EMPTY_DIGEST
    with_network = AgentSpec.model_validate({**LEGACY_SPEC, "network": {"domains": []}})
    assert empreinte(with_network) != LEGACY_DIGEST, "a declared network is part of what the version IS"
    assert empreinte(AgentSpec(network=AgentNetwork(builtin_web_tools=False))) != EMPTY_DIGEST


# ───────────────────────── the run's effective network and its decisions ─────────────────────────


async def test_a_run_reads_its_own_effective_network_with_its_own_token_only(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await client.put(
        f"/api/v1/projects/{project['id']}/policy",
        json={"yaml": _policy(["github.com", "pypi.org", "files.pythonhosted.org"], per_host_per_minute=30)},
    )
    await _ceiling(client, {"deny_domains": ["files.pythonhosted.org"], "per_host_per_minute": 60})
    agent = {"instructions": "x", "network": {"domains": ["pypi.org"], "per_host_per_minute": 10}}
    assert (
        await client.post(AGENTS, json={"slug": "pip", "display_name": "P", "spec": agent})
    ).status_code == 201
    mine = await _run(project, "run-net-1", agent_slug="pip", agent_version=1)
    other = await _run(project, "run-net-2")

    got = await client.get("/api/v1/internal/runs/run-net-1/network", headers=mine)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["allow"] == ["pypi.org"], "the agent narrows the project's list"
    assert body["deny"] == ["files.pythonhosted.org"]
    assert body["implied"] == ["github.com"]
    assert (body["per_host_per_minute"], body["org_per_host_per_minute"]) == (10, 60)
    assert (body["org"], body["project"], body["agent"], body["listed"]) == (
        "varga",
        "billing-api",
        "pip",
        True,
    )
    assert body["builtin_web_tools"] is True

    refused = await client.get("/api/v1/internal/runs/run-net-1/network", headers=other)
    assert refused.status_code == 403, "another run's token"
    assert (await client.get("/api/v1/internal/runs/run-net-1/network")).status_code == 401

    plain = (await client.get("/api/v1/internal/runs/run-net-2/network", headers=other)).json()
    assert plain["allow"] == ["github.com", "pypi.org"], "no agent: the project's list, minus the denied host"


async def test_a_trial_run_takes_the_network_the_orchestrator_wrote_into_its_permissions(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": _policy(["pypi.org"])})
    headers = await _run(
        project,
        "run-trial-1",
        agent_slug="trial",
        agent_version=0,
        stage_input={
            "permissions": {
                "egress": "listed",
                "allow_domains": ["pypi.org", "api.stripe.com", "github.com"],
                "builtin_web_tools": False,
            }
        },
    )
    body = (await client.get("/api/v1/internal/runs/run-trial-1/network", headers=headers)).json()
    assert body["allow"] == ["pypi.org"], "a host the project does not allow never opens; github is implied"
    assert body["implied"] == ["github.com"]
    assert body["builtin_web_tools"] is False


def _batch(*decisions: dict[str, Any]) -> dict[str, Any]:
    return {"decisions": list(decisions)}


DENIED = {
    "layer": "proxy",
    "host": "Evil.Example.",
    "port": 443,
    "decision": "denied",
    "reason": "not_allowed",
}
ALLOWED = {
    "layer": "proxy",
    "host": "pypi.org",
    "port": 443,
    "decision": "allowed",
    "reason": "allowed:pypi.org",
}


async def test_decisions_are_cumulative_and_a_replayed_batch_does_not_double_them(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import NetworkDecision
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    headers = await _run(project, "run-dec-1", agent_slug="pip", agent_version=1)
    url = "/api/v1/internal/runs/run-dec-1/network/decisions"
    first = _batch({**ALLOWED, "count": 3, "bytes_up": 10, "bytes_down": 900}, {**DENIED, "count": 1})
    assert (await client.post(url, headers=headers, json=first)).json() == {"accepted": 2}
    assert (await client.post(url, headers=headers, json=first)).status_code == 202
    later = _batch({**ALLOWED, "count": 5, "bytes_up": 20, "bytes_down": 1500})
    assert (await client.post(url, headers=headers, json=later)).status_code == 202

    async with session_scope() as session:
        rows = (await session.execute(select(NetworkDecision).order_by(NetworkDecision.host))).scalars().all()
    assert [(r.host, r.decision, r.count, r.bytes_up, r.bytes_down) for r in rows] == [
        ("evil.example", "denied", 1, 0, 0),
        ("pypi.org", "allowed", 5, 20, 1500),
    ], "one row per key, the totals replaced"
    assert rows[0].agent_slug == "pip"

    assert (
        await client.post(url, headers=headers, json={"decisions": [{**ALLOWED, "layer": "other"}]})
    ).status_code == 422


async def test_the_first_denial_of_a_host_is_journaled_once_per_run(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    headers = await _run(project, "run-dec-2")
    url = "/api/v1/internal/runs/run-dec-2/network/decisions"
    await client.post(url, headers=headers, json=_batch({**DENIED, "count": 1}))
    await client.post(url, headers=headers, json=_batch({**DENIED, "count": 4}))
    await client.post(
        url, headers=headers, json=_batch({**DENIED, "reason": "denied:evil.example", "count": 1})
    )
    await client.post(url, headers=headers, json=_batch({**DENIED, "host": "other.example", "count": 1}))
    async with session_scope() as session:
        events = (
            (await session.execute(select(Event).where(Event.type == "choregos.network.denied")))
            .scalars()
            .all()
        )
    assert sorted(e.payload["host"] for e in events) == ["evil.example", "other.example"]
    assert events[0].payload["run_id"] == "run-dec-2" and events[0].payload["layer"] == "proxy"


# ───────────────────────── the organisation's view ─────────────────────────


async def test_network_activity_groups_by_host_and_is_refused_without_audit_read(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    for run_id in ("run-act-1", "run-act-2"):
        headers = await _run(project, run_id, agent_slug="pip", agent_version=1)
        url = f"/api/v1/internal/runs/{run_id}/network/decisions"
        await client.post(url, headers=headers, json=_batch({**ALLOWED, "count": 2}, {**DENIED, "count": 3}))

    activity = await client.get("/api/v1/orgs/varga/network-activity")
    assert activity.status_code == 200, activity.text
    rows = {(r["host"], r["decision"]): r for r in activity.json()["rows"]}
    assert rows[("pypi.org", "allowed")]["count"] == 4 and rows[("pypi.org", "allowed")]["runs"] == 2
    assert rows[("evil.example", "denied")]["projects"] == ["billing-api"]
    assert rows[("evil.example", "denied")]["agents"] == ["pip"]
    assert rows[("evil.example", "denied")]["layer"] == "proxy"

    only = (await client.get("/api/v1/orgs/varga/network-activity", params={"decision": "denied"})).json()
    assert [r["host"] for r in only["rows"]] == ["evil.example"]
    future = {
        "since": (
            __import__("datetime").datetime.now(__import__("datetime").UTC) + timedelta(days=1)
        ).isoformat()
    }
    assert (await client.get("/api/v1/orgs/varga/network-activity", params=future)).json() == {"rows": []}
    assert (await client.get("/api/v1/orgs/varga/network-activity", params={"project": "nope"})).json() == {
        "rows": []
    }

    added = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "dev@varga.dev", "role": "developer"}
    )
    assert added.status_code in (200, 201), added.text
    await login(client, "dev@varga.dev")
    refused = await client.get("/api/v1/orgs/varga/network-activity")
    assert refused.status_code == 403, refused.text
